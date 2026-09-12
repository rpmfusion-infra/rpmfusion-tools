#!/usr/bin/env python3
#
# koji_override.py - Automate buildroot override requests.
#
# Accepts either a build ID or an NVR, automatically determines the
# appropriate tag, requests a buildroot override, and waits until the
# package becomes available in the repository.
#

import sys
import os
# import logging
import koji
from koji_cli.lib import watch_tasks, wait_repo

# logging.basicConfig(logging.DEBUG)
# logger = logging.getLogger("koji.build")
# logger.setLevel(logging.DEBUG)

session = koji.ClientSession('https://koji.rpmfusion.org/kojihub')
cert_path = os.path.expanduser('~/.rpmfusion.cert')
session.ssl_login(cert_path, None, None)


def tag_build(tag_name, nvr):
    print(f"Tagging build '{nvr}' into tag '{tag_name}'...")
    task_id = session.tagBuild(tag_name, nvr)
    ret = watch_tasks(session, [task_id], quiet=False, poll_interval=10)
    return ret == 0


def koji_wait_repo(tag_name, nvr):
    print(f"Waiting for repo '{tag_name}' to include build '{nvr}'...")

    tag_info = session.getTag(tag_name)
    if not tag_info:
        print(f"Tag '{tag_name}' not found.")
        return False

    build_info = session.getBuild(nvr)
    if not build_info:
        print(f"Build '{nvr}' not found.")
        return False

    ok, msg = wait_repo(session, tag_info['id'], builds=[build_info])
    print(msg)
    if ok:
        print(f"Repo '{tag_name}' now includes build '{nvr}'.")
    else:
        print(f"Timeout or failure waiting for repo '{tag_name}' to include '{nvr}'.")
    return ok


def get_build_and_tag_from_buildinfo(buildname):
    """Get build NVR and current updates-candidate tag using Koji API instead of scraping HTML."""

    build_info = session.getBuild(buildname)
    if not build_info:
        return None, None
    nvr = build_info['nvr']
    build_id = build_info['build_id']

    tags = session.listTags(build=int(build_id))
    candidate_tag = None
    tag_type = 'candidate'
    for t in tags:
        # print(f'tag = {t}')
        if t['name'].endswith('-updates-candidate'):
            candidate_tag = t['name'].removesuffix('-updates-candidate')
        elif t['name'].endswith('-candidate'):
            candidate_tag = t['name'].removesuffix('-candidate')
        if t['name'].endswith('-override'):
            candidate_tag = t['name'].removesuffix('-override')
            tag_type = 'override'
    return nvr, candidate_tag, tag_type


def main():
    print(f"Usage: {os.path.basename(sys.argv[0])} buildID or nvr [buildID or nvr ...]")
    print("Automatically request a buildroot override and wait until the package is available.")

    args = sys.argv[1:]
    for arg in args:
        if arg.isdigit():
            if not (5 <= len(arg) <= 6):
                print(f"The argument '{arg}' is not a number with 5 or 6 digits.")
                sys.exit(1)
            buildname = int(arg)
        else:
            buildname = arg

        nvr, candidate_tag, tag_type = get_build_and_tag_from_buildinfo(buildname)
        if not nvr:
            print(f"Could not determine NVR for build {buildname}.")
            continue
        if not candidate_tag:
            print(f"Could not determine tag for build {buildname}.")
        else:
            if tag_type == 'override':
                print(f"{nvr} is already tagged into {candidate_tag}-override, skipping...")
                tag_build_name = candidate_tag + '-build'
            else:
                print(f"Build: {nvr}, candidate tag: {candidate_tag}-candidate")
                tag_build_name = candidate_tag + '-build'

                tag_override = candidate_tag + '-override'
                if not tag_build(tag_override, nvr):
                    print(f"Failed to tag {nvr} into {tag_override}, skipping.")
                    continue

            koji_wait_repo(tag_build_name, nvr)


if __name__ == '__main__':
    main()
