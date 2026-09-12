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
import argparse
import json
from zoneinfo import ZoneInfo
from datetime import datetime
import rpm
import koji
from koji_cli.lib import watch_tasks

MOCK_STATUS_30_PATTERN = "mock exited with status 30"
POLL_INTERVAL = 10
dt_paris = int(datetime.now(ZoneInfo('Europe/Paris')).utcoffset().total_seconds())


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
        # task_info = session.getTaskInfo(int(child['id']), request=True)
        state = child.get('state')
        method = child.get('method', '')
        request = child.get('request', [])
        # print(f"task {child['id']} state {state} method {method} request {request}")
        # buildSRPMFromSCM result contains the SRPM filename with NVR
        if state == koji.TASK_STATES['CLOSED'] and method == 'buildSRPMFromSCM':
            result = session.getTaskResult(child['id'], raise_fault=False)
            # print(json.dumps(result))
            if isinstance(result, dict) and 'srpm' in result:
                srpm = os.path.basename(result['srpm'])  # foo-1.0-1.fc40.src.rpm
                hdr = srpm.rsplit('.', 2)[0]             # foo-1.0-1.fc40
                # parse NVR: split from right on '-' twice
                parts = hdr.rsplit('-', 2)
                if len(parts) == 3:
                    print(f"return nvr from buildSRPMFromSCM task {child['id']}")
                    return parts[0], '0', parts[1], parts[2]
        # buildArch request[0] is the SRPM path, also contains NVR
        if method == 'buildArch':
            if request:
                srpm = os.path.basename(request[0])  # foo-1.0-1.fc40.src.rpm
                hdr = srpm.rsplit('.', 2)[0]
                parts = hdr.rsplit('-', 2)
                if len(parts) == 3:
                    print(f"return nvr from buildArch task {child['id']}")
                    return parts[0], '0', parts[1], parts[2]

    # No children or could not parse, extract only package name from SCM URL
    print("Task has no children or the version could not be determined. Extract only the package name from the SCM URL and use the build tag name.")
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

def repo_was_regenerated_after(build_tag_name, after_ts):
    """Check if a newRepo task for this build tag completed successfully after after_ts."""

    newrepo_opts = {
        'method': 'newRepo',
        'state': [koji.TASK_STATES['CLOSED']],
        'completeAfter': after_ts - dt_paris,
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

    # Extract dist from task_release (e.g., "1.fc44" -> "fc44", "1.git90261ae.el9" -> "el9")
    task_dist = None
    if task_release:
        m = re.search(r'\.(fc\d+|el\d+[^.]*)', task_release)
        if m:
            task_dist = m.group(1)

    task_info_ts = session.getTaskInfo(task_id, request=False)
    fail_ts = task_info_ts.get('create_ts')

    if task_version is not None:
        print(f"build_tag_name = {build_tag_name}, nvr = {pkg_name}-{task_epoch}:{task_version}-{task_release}.{task_dist}")
        existing_builds = session.listBuilds(
            packageID=pkg_id,
            state=koji.BUILD_STATES['COMPLETE'],
            queryOpts={'order': '-build_id', 'createdAfter': fail_ts - dt_paris},
        )
        # print(json.dumps(existing_builds))
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
        updates_tag = build_tag_name.removesuffix('-build').removesuffix('-multilibs')
        print(f"build_tag_name = {build_tag_name}, updates_tag = {updates_tag}, pkg_name = {pkg_name}")
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

        for tag in tags_to_check:
            existing_builds = session.listTagged(
                tag,
                package=pkg_name,
                inherit=True,
                latest=False,
            )

            for b in existing_builds:
                # Filter only builds completed after the task failure
                if b['task_id'] is None:
                    continue
                task2 = session.getTaskInfo(b['task_id'], request=False)
                #print(f" existing builds taks {b['task_id']} = {task2}")
                if (task2.get('completion_ts') or 0) > fail_ts:
                    # Only have package name — if ANY complete build exists, skip
                    print(f"  [skip] {pkg_name} with {build_tag_name} has an unknown NVR, but we got already a new build: {b['nvr']}, task_id {b['task_id']}")
                    return True

    # Check active tasks for same source
    task_info = session.getTaskInfo(int(task_id), request=True)
    source = task_info.get('request', [None])[0]
    print(f"source = {source}")
    source_url = source.split('#')[0]
    active_opts = {
        'method': 'build',
        'state': [
            koji.TASK_STATES['FREE'],
            koji.TASK_STATES['OPEN'],
            koji.TASK_STATES['ASSIGNED'],
            koji.TASK_STATES['FAILED'],
        ],
        'createdAfter': fail_ts - dt_paris,
    }
    active_tasks = session.listTasks(opts=active_opts, queryOpts={'order': 'id'})
    for t in active_tasks:
        if t['id'] <= int(task_id):
            continue
        t_info = session.getTaskInfo(t['id'], request=True)
        t_request = t_info.get('request', [])
        if not t_request or len(t_request) < 2:
            continue
        t_target_info = session.getBuildTarget(t_request[1])
        if not t_target_info:
            continue
        t_request_url = t_request[0].split('#')[0]
        if t_request_url == source_url and t_target_info['build_tag_name'] == build_tag_name:
            print(f"  [skip] {pkg_name} already being built in task {t['id']}")
            return True

    return False

def get_task_error_message(task_id):
    try :
        result = session.getTaskResult(task_id, raise_fault=False)
        if isinstance(result, dict):
            return result.get('faultString', '')
        print('get_task_error_message result is not a dictonary !')
    except koji.GenericError as e:
        return (f'koji.GenericError: {e}')
    return ''


def get_task_root_log(task_id):
    try:
        root_log = session.downloadTaskOutput(task_id, 'root.log')

        if isinstance(root_log, bytes):
            return root_log

        return b''

    except Exception as exc:
        print(f'Could not retrieve root.log for task {task_id}: {exc}')
        return b''


def is_mock_status_30(task_id):
    print_default_msg = True
    children = session.getTaskChildren(task_id, request=False)
    for child in children:
        if child.get('state') == koji.TASK_STATES['FAILED']:
            err = get_task_error_message(child['id'])
            perr = {err}
            print(f'Child task {child['id']}, message: {perr}')
            if MOCK_STATUS_30_PATTERN in err:
                root_log = get_task_root_log(child['id'])
                if b'No match for argument' in root_log:
                    if print_default_msg:
                        print(f" [skip] [task {task_id}] Failed with \"No match for argument\" is not that we are looking for")
                        print_default_msg = False
                elif b'nothing provides' in root_log:
                        print(f" [skip] [task {task_id}] Failed with \"nothing provides\" is not that we are looking for")
                        print_default_msg = False
                else:
                    return True
        elif child.get('state') == koji.TASK_STATES['CANCELED']:
            if print_default_msg:
                print(f'Child task {child['id']}, with state canceled')
                print_default_msg = False

    #err = get_task_error_message(task_id)
    #perr = {err}
    #print(f'Parent task {task_id}, message: {perr}')
    #if MOCK_STATUS_30_PATTERN in err:
    #    return True

    if print_default_msg:
        print(f" [skip] [task {task_id}] Failed but not with mock status 30.")
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


def handle_failed_task(task, regenned_tags, confirm=False):
    task_id = int(task['id'])
    task_info = session.getTaskInfo(task_id, request=True)

    # Get build tag name
    request = task_info.get('request', [])
    target = request[1]
    target_info = session.getBuildTarget(target)
    build_tag_name = target_info['build_tag_name']

    if not build_tag_name:
        print(f"  [skip] [task {task_id}] Could not determine build_tag_name.")
        return

    if is_scratch_build(task_info):
        print(f" [skip] [task {task_id}] Scratch build, skipping.")
        return

    if not is_mock_status_30(task_id):
        return

    print(f"[task {task_id}] Failed with mock status 30.")

    # print(f" task {task_id} = {task}")
    if is_already_built_or_building(task_id, build_tag_name):
        return

    if confirm :
        answer = input("Press Enter to continue or s to skip [Enter / s to skip]")
        if answer.lower() == 's':
            print("    Skipped.")
            return
        print("    Continuing...")

    #task_info_ts = session.getTaskInfo(task_id, request=False)
    fail_ts = task_info.get('start_ts')

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

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch failed build tasks and retry mock status 30 failures."
    )
    parser.add_argument(
        '--hours', type=int, default=48,
        help="How many hours back to look for failed tasks (default: 48)."
    )
    parser.add_argument(
        '--confirm', action='store_true',
        help="Ask for confirmation before each resubmit/regen operation."
    )
    return parser.parse_args()

def main():
    args = parse_args()
    mode = "manual confirmation for each operation" if args.confirm else "unattended mode (no confirmation)"
    print(f"It will check failed build task(s) in the last {args.hours} hours, running in {mode}.")
    regenned_tags = set()

    hours_ago = int(time.time()) - args.hours * 60 * 60

    failed_opts = {
        'method': 'build',
        'state': [koji.TASK_STATES['FAILED'], koji.TASK_STATES['CANCELED']],
        'completeAfter': hours_ago - dt_paris,
    }
    failed_tasks = session.listTasks(opts=failed_opts, queryOpts={'order': 'id'})

    print(f"Found {len(failed_tasks)} failed build task(s) in the last {args.hours} hours.")

    # print("Failed or canceled tasks found:")
    # for task in failed_tasks:
    #     print(f"  Task {task['id']}")
    for task in failed_tasks:
        print(f"\n  Failed or canceled task found: {task['id']}")
        handle_failed_task(task, regenned_tags, confirm=args.confirm)

    print("\nDone.")


if __name__ == '__main__':
    main()

