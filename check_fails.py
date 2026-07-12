#!/usr/bin/python3
"""
Fetch failed build tasks from the last n hours.
For each task failed with mock exit status 30:
  1. Resubmit once
  2. If it fails again with status 30, regen repo (once per build tag) and resubmit again
"""

import os
import re
import time
import json
import rpm
import koji
from koji_cli.lib import watch_tasks

MOCK_STATUS_30_PATTERN = "mock exited with status 30"
POLL_INTERVAL = 10

session = koji.ClientSession('https://koji.rpmfusion.org/kojihub')
cert_path = os.path.expanduser('~/.rpmfusion.cert')
session.ssl_login(cert_path, None, None)


def get_nvr_from_task(task_id):
    """
    Try to get NVR from children tasks (buildSRPMFromSCM result or buildArch request).
    Falls back to extracting just the package name from the SCM URL.
    Returns (pkg_name, epoch, version, release) or (pkg_name, None, None, None) if only name known.
    """
    children = session.getTaskChildren(int(task_id), request=True)
    for child in children:
        task_info = session.getTaskInfo(int(task_id), request=False)
        state = task_info.get('state')
        if state != koji.TASK_STATES['CLOSED']:
            continue
        method = child.get('method', '')
        # buildSRPMFromSCM result contains the SRPM filename with NVR
        if method == 'buildSRPMFromSCM':
            result = session.getTaskResult(child['id'], raise_fault=False)
            # print(json.dumps(result))
            if isinstance(result, dict) and 'srpm' in result:
                srpm = os.path.basename(result['srpm'])  # foo-1.0-1.fc40.src.rpm
                hdr = srpm.rsplit('.', 2)[0]             # foo-1.0-1.fc40
                # parse NVR: split from right on '-' twice
                parts = hdr.rsplit('-', 2)
                if len(parts) == 3:
                    return parts[0], '0', parts[1], parts[2]
        # buildArch request[0] is the SRPM path, also contains NVR
        if method == 'buildArch':
            child_request = child.get('request', [])
            if child_request:
                srpm = os.path.basename(child_request[0])  # foo-1.0-1.fc40.src.rpm
                hdr = srpm.rsplit('.', 2)[0]
                parts = hdr.rsplit('-', 2)
                if len(parts) == 3:
                    return parts[0], '0', parts[1], parts[2]

    # No children or could not parse — extract only package name from SCM URL
    task_info = session.getTaskInfo(int(task_id), request=True)
    request = task_info.get('request', [])
    if request:
        url = request[0]  # git+https://pkgs.rpmfusion.org/git/free/telegram-desktop#abc123
        pkg_name = url.rstrip('/').split('/')[-1].split('#')[0]
        # target   = request[1]   # 'f45-nonfree' (the target name, not the tag)
        # options  = request[2]
        return pkg_name, None, None, None

    return None, None, None, None


def is_scratch_build(task_info):
    request = task_info.get('request', [])
    if len(request) >= 3 and isinstance(request[2], dict):
        return request[2].get('scratch', False)
    return False

def get_build_tag_for_task(task_info):
    request = task_info.get('request', [])
    if len(request) < 2:
        return None, None
    target = request[1]
    target_info = session.getBuildTarget(target)
    if not target_info:
        return None, None
    return target_info['build_tag_name'], target

def repo_was_regenerated_after(build_tag_name, after_ts):
    """Check if a newRepo task for this build tag completed successfully after after_ts."""

    newrepo_opts = {
        'method': 'newRepo',
        'state': [koji.TASK_STATES['CLOSED']],
        'completeAfter': after_ts,
    }

    tasks = session.listTasks(opts=newrepo_opts, queryOpts={'order': '-id'})
    for t in tasks:
        t_info = session.getTaskInfo(t['id'], request=True)
        t_request = t_info.get('request', [])
        # newRepo request[0] is the tag_id
        if t_request and t_request[0] == build_tag_name:
            return True
    return False

def is_already_built_or_building(task_id, build_tag_name):

    pkg_name, task_epoch, task_version, task_release = get_nvr_from_task(task_id)
    if not pkg_name:
        print(f"  [skip check] Could not determine package name for task {task_id}")
        return True

    pkg_id = session.getPackageID(pkg_name)
    if not pkg_id:
        print(f"  [skip check] Package '{pkg_name}' not found in koji")
        return True

    # tag_info = session.getTag(build_tag_name)
    # tag_id = tag_info['id']

    # Extrair dist da task_release (ex: "1.fc44" -> "fc44", "1.git90261ae.el9" -> "el9")
    task_dist = None
    if task_release:
        m = re.search(r'\.(fc\d+|el\d+[^.]*)', task_release)
        if m:
            task_dist = m.group(1)

    print(f"task {task_id} name {pkg_name} {build_tag_name} task_dist {task_dist} task_epoch {task_epoch}, task_version {task_version}, task_release {task_release}")
    existing_builds = session.listBuilds(
        packageID=pkg_id,
        state=koji.BUILD_STATES['COMPLETE'],
        queryOpts={'order': '-build_id'}
    )
    # print(json.dumps(existing_builds))

    if task_version is not None:
        for b in existing_builds:
            b_epoch   = str(b.get('epoch') or '0')
            b_version = b['version']
            b_release = b['release']

            # Filtrar pela mesma dist
            if task_dist:
                m_b = re.search(r'\.(fc\d+|el\d+[^.]*)', b_release)
                b_dist = m_b.group(1) if m_b else None
                if b_dist != task_dist:
                    continue

            # hack forget epoch
            b_epoch = '0'
            #print(f"{task_epoch} {task_version} {task_release}")
            #print(f"{b_epoch} {b_version} {b_release}")
            cmp = rpm.labelCompare(
                (str(task_epoch), task_version, task_release),
                (b_epoch, b_version, b_release)
            )
            if cmp <= 0:
                print(f"  [skip] {pkg_name} already built with equal or newer NVR: {b['nvr']}")
                return True
    else:
        task_info_ts = session.getTaskInfo(task_id, request=False)
        fail_ts = task_info_ts.get('create_ts')
        print(f"no task version build_tag_name = {build_tag_name}")
        updates_tag = build_tag_name.removesuffix('-build').removesuffix('-multilibs')
        if updates_tag.startswith('el'):
            tags_to_check = [
                updates_tag + '-candidate',
                updates_tag + '-testing',
            ]
        else:  # fedora (f43, etc.)
            tags_to_check = [
                updates_tag + '-updates-candidate',
                updates_tag + '-updates-testing',
            ]
        print(f"no task version updates_tag = {updates_tag}")

        for tag in tags_to_check:
            existing_builds = session.listTagged(
                tag,
                package=pkg_name,
                inherit=True,
                latest=False,
            )

            for b in existing_builds:
                # Filtrar apenas builds completados após a falha da task
                if b['task_id'] is None:
                    continue
                task2 = session.getTaskInfo(b['task_id'], request=False)
                #print(f" existing builds taks {b['task_id']} = {task2}")
                if (task2.get('completion_ts') or 0) > fail_ts:
                    # Only have package name — if ANY complete build exists, skip
                    print(f"  [skip] {pkg_name} with {build_tag_name} has NVR unknown, and we got already a new build {b['nvr']} task_id {b['task_id']} skipping.")
                    return True

    # Check active tasks for same source
    task_info = session.getTaskInfo(int(task_id), request=True)
    source = task_info.get('request', [None])[0]
    print(f"source = {source}")
    active_opts = {
        'method': 'build',
        'state': [
            koji.TASK_STATES['FREE'],
            koji.TASK_STATES['OPEN'],
            koji.TASK_STATES['ASSIGNED'],
        ],
    }
    active_tasks = session.listTasks(opts=active_opts)
    for t in active_tasks:
        if t['id'] == int(task_id):
            continue
        t_info = session.getTaskInfo(t['id'], request=True)
        t_request = t_info.get('request', [])
        if not t_request or len(t_request) < 2:
            continue
        t_target_info = session.getBuildTarget(t_request[1])
        if not t_target_info:
            continue
        if t_request[0] == source and t_target_info['build_tag_name'] == build_tag_name:
            print(f"  [skip] {pkg_name} already being built in task {t['id']}")
            return True

    return False

def get_task_error_message(task_id):
    try :
        result = session.getTaskResult(task_id, raise_fault=False)
        if isinstance(result, dict):
            print(f'task_error_message {task_id}: {result}')
            return result.get('faultString', '')
        print('get_task_error_message result is not a dictonary !')
    except koji.GenericError as e:
        print(f'task_error_message exception {task_id}: {e}')
    return ''


def is_mock_status_30(task_id):
    err = get_task_error_message(task_id)
    if MOCK_STATUS_30_PATTERN in err:
        return True
    children = session.getTaskChildren(task_id, request=False)
    for child in children:
        if child.get('state') == koji.TASK_STATES['FAILED']:
            print(f'Child task message {child['id']}')
            child_err = get_task_error_message(child['id'])
            if MOCK_STATUS_30_PATTERN in child_err:
                    return True
    return False


def regen_repo(build_tag_name):
    print(f"  [regen] Regenerating repo for: {build_tag_name}")
    new_task_id = session.newRepo(build_tag_name)
    ret = watch_tasks(session, [new_task_id], quiet=False, poll_interval=POLL_INTERVAL)
    if ret != 0:
        print(f"  [regen] ERROR: newRepo failed for {build_tag_name}")
        return False
    print(f"  [regen] Repo regenerated OK for {build_tag_name}")
    return True


def handle_failed_task(task, regenned_tags):
    task_id = int(task['id'])
    #task2 = session.getTaskInfo(task['id'], request=False)
    #print(f" task2 {task['id']} = {task2}")

    task_info = session.getTaskInfo(task_id, request=True)

    # Get build tag name
    build_tag_name, _ = get_build_tag_for_task(task_info)
    if not build_tag_name:
        print(f"  [task {task_id}] Could not determine build_tag_name, skipping.")
        return

    if is_scratch_build(task_info):
        print(f"[task {task_id}] Scratch build, skipping.")
        return

    if not is_mock_status_30(task_id):
        return

    print(f"[task {task_id}] Failed with mock status 30.")

    # print(f" task {task_id} = {task}")
    if is_already_built_or_building(task_id, build_tag_name):
        return

    # confirm = input("Press Enter to continue or s to skip [Enter / s to skip]")
    # if confirm.lower() == 's':
    #     print("    Skipped.")
    #     return

    #task_info_ts = session.getTaskInfo(task_id, request=False)
    fail_ts = task_info.get('completion_ts')

    if repo_was_regenerated_after(build_tag_name, fail_ts):
        # --- Attempt 1: resubmit directly ---
        print(f"[task {task_id}] Resubmitting (attempt 1)...")
        new_task_id = session.resubmitTask(task_id)
        print(f"[task {task_id}] Resubmitted as task: {new_task_id}")

        ret = watch_tasks(session, [new_task_id], quiet=False, poll_interval=POLL_INTERVAL)
        if ret == 0:
            print(f"[task {new_task_id}] OK on first resubmit.")
            return

        if not is_mock_status_30(int(new_task_id)):
            print(f"[task {new_task_id}] Failed, but NOT with mock status 30. Skipping.")
            return

        print(f"[task {new_task_id}] Failed again with mock status 30.")
    else:
        new_task_id = task_id

    # --- Regen repo (once per build tag) ---
    if build_tag_name not in regenned_tags:
        ok = regen_repo(build_tag_name)
        regenned_tags.add(build_tag_name)
        if not ok:
            print(f"[task {new_task_id}] Repo regen failed, skipping final resubmit.")
            return
    else:
        print(f"  [regen] '{build_tag_name}' already regenerated, skipping.")

    # --- Attempt 2: resubmit after regen ---
    print(f"[task {new_task_id}] Resubmitting (attempt 2, after regen)...")
    new_task_id2 = session.resubmitTask(new_task_id)
    print(f"[task {new_task_id}] Resubmitted as task: {new_task_id2}")

    ret = watch_tasks(session, [new_task_id2], quiet=False, poll_interval=POLL_INTERVAL)
    if ret == 0:
        print(f"[task {new_task_id2}] OK on second resubmit.")
    else:
        print(f"[task {new_task_id2}] Still failing after repo regen. Manual intervention needed.")


def main():
    regenned_tags = set()

    hours = 72
    hours_ago = int(time.time()) - hours * 60 * 60

    failed_opts = {
        'method': 'build',
        'state': [koji.TASK_STATES['FAILED'], koji.TASK_STATES['CANCELED']],
        'completeAfter': hours_ago,
    }
    failed_tasks = session.listTasks(opts=failed_opts, queryOpts={'order': 'id'})

    print(f"Found {len(failed_tasks)} failed build task(s) in the last {hours} hours.")

    # print("Failed or canceled tasks found:")
    # for task in failed_tasks:
    #     print(f"  Task {task['id']}")
    for task in failed_tasks:
        print(f"\n  Failed or canceled task found: {task['id']}")
        handle_failed_task(task, regenned_tags)

    print("\nDone.")


if __name__ == '__main__':
    main()

