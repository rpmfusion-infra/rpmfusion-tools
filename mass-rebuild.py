#!/usr/bin/python3
#
# mass-rebuild.py - A utility to rebuild packages.
#
# Copyright (C) 2009-2013 Red Hat, Inc.
# SPDX-License-Identifier:      GPL-2.0+
#
# Authors:
#     Jesse Keating <jkeating@redhat.com>
#

import koji
import os
import re
import subprocess
import time
import operator

# Set some variables
max_concurrent_builds = 5
flavors = ["free", "nonfree"]
# Some of these could arguably be passed in as args.
tag = 'f45'
epoch = '2026-07-30 00:00:00' # rebuild anything not built after this date
user = 'RPM Fusion Release Engineering <leigh123linux@rpmfusion.org>'
comment = '- Rebuilt for https://fedoraproject.org/wiki/Fedora_45_Mass_Rebuild'
local_workdir = os.path.expanduser('~/rpmfusion/new/massrebuild/')

pkg_skip_list = ['rpmfusion-free-release', 'rpmfusion-nonfree-release', 'buildsys-build-rpmfusion',
'rpmfusion-free-appstream-data', 'rpmfusion-nonfree-appstream-data',
'rfpkg-minimal', 'lpf-cleartype-fonts', 'lpf-mscore-fonts', 'lpf-mscore-tahoma-fonts',
'lpf-spotify-client', 'mock-rpmfusion-free', 'mock-rpmfusion-nonfree', 'rpmfusion-free-obsolete-packages',
'rpmfusion-nonfree-obsolete-packages', 'rpmfusion-free-remix-kickstarts', 'rpmfusion-nonfree-remix-kickstarts',
'wormsofprey-data']

# koji task states that mean "this task is no longer running"
DONE_STATES = {koji.TASK_STATES['CLOSED'],
               koji.TASK_STATES['CANCELED'],
               koji.TASK_STATES['FAILED']}

# Define functions

# This function needs a dry-run like option
def runme(cmd, action, pkg, env, cwd):
    """Simple function to run a command and return 0 for success, 1 for
       failure.  cmd is a list of the command and arguments, action is a
       name for the action (for logging), pkg is the name of the package
       being operated on, env is the environment dict, and cwd is where
       the script should be executed from."""

    try:
        subprocess.check_call(cmd, env=env, cwd=cwd)
    except subprocess.CalledProcessError as e:
        print('%s failed %s: %s\n' % (pkg, action, e))
        return 1
    return 0

# This function needs a dry-run like option
def runmeoutput(cmd, action, pkg, env, cwd):
    """Simple function to run a command and return output if successful.
       cmd is a list of the command and arguments, action is a
       name for the action (for logging), pkg is the name of the package
       being operated on, env is the environment dict, and cwd is where
       the script should be executed from.  Returns 0 for failure"""

    try:
        pid = subprocess.Popen(cmd, env=env, cwd=cwd,
                               stdout=subprocess.PIPE, encoding='utf8')
    except BaseException as e:
        print('%s failed %s: %s\n' % (pkg, action, e))
        return 0
    result = pid.communicate()[0].rstrip('\n')
    return result

def prune_finished(kojisession, active_tasks):
    """Remove any task ids from active_tasks that have finished
       (closed, canceled, or failed). Returns the still-running set."""

    still_running = set()
    for tid in active_tasks:
        try:
            info = kojisession.getTaskInfo(tid)
        except Exception as e:
            print('Could not query task %s: %s' % (tid, e))
            # If we can't query it, assume it's still running rather than
            # silently dropping it from tracking.
            still_running.add(tid)
            continue
        if info and info['state'] not in DONE_STATES:
            still_running.add(tid)
    return still_running


def wait_for_room(kojisession, active_tasks, max_concurrent, interval=2*60):
    """Block until fewer than max_concurrent of active_tasks are still
       running, pruning finished tasks along the way. Returns the
       updated (pruned) active_tasks set."""

    active_tasks = prune_finished(kojisession, active_tasks)
    while len(active_tasks) >= max_concurrent:
        print('%d task(s) currently running (limit %d): %s'
              % (len(active_tasks), max_concurrent, sorted(active_tasks)))
        time.sleep(interval)
        active_tasks = prune_finished(kojisession, active_tasks)
    return active_tasks


def wait_for_tasks(kojisession, task_ids, interval=5*60):
    """Poll koji until every task_id in this batch has finished
       (closed, canceled, or failed). task_ids is a list/set of
       koji task ids. Used at the very end to drain any stragglers."""

    pending = set(task_ids)
    if not pending:
        return

    print('Waiting for final %d task(s) to complete: %s'
          % (len(pending), sorted(pending)))

    while pending:
        pending = prune_finished(kojisession, pending)
        if pending:
            print('Still waiting on %d task(s): %s'
                  % (len(pending), sorted(pending)))
            time.sleep(interval)

    print('All %d task(s) finished.' % len(task_ids))


def mass_rebuild(tag, workdir, flavor):
    enviro = os.environ

    target = '%s-%s' % (tag, flavor)
    buildtag = '%s-build' % target  # tag to build from
    targets = ['%s-candidate' % target , 'rawhide-%s' % flavor, '%s' % target] # tag to build from
    # check builds on multilibs targets ...
    targets += ['rawhide-%s-multilibs' % flavor]

    # Create a koji session
    kojisession = koji.ClientSession('https://koji.rpmfusion.org/kojihub')

    # Generate a list of packages to iterate over
    pkgs = kojisession.listPackages(buildtag, inherited=True)

    # reduce the list to those that are not blocked and sort by package name
    pkgs = sorted([pkg for pkg in pkgs if (not pkg['blocked'] and pkg['tag_name'] == target)],
                key=operator.itemgetter('package_name'))

    for pkg in pkgs:
        name = pkg['package_name']
        if name not in pkg_skip_list:
            print("%s" % (name))

    print('Checking %s packages...' % len(pkgs))
    print('massrebuild all packages since %s, target %s, workdir %s' % (target, epoch, workdir))

    active_tasks = set()

    # Loop over each package
    for pkg in pkgs:
        name = pkg['package_name']
        pkg_id = pkg['package_id']

        # some package we just dont want to ever rebuild
        if name in pkg_skip_list:
            print('Skipping %s, package is explicitely skipped' % name)
            continue

        # Query to see if a build has already been attempted
        # this version requires newer koji:
        builds = kojisession.listBuilds(pkg_id, createdAfter=epoch)
        newbuild = False
        # Check the builds to make sure they were for the target we care about
        for build in builds:
            try:
                buildtarget = kojisession.getTaskInfo(build['task_id'],
                                           request=True)['request'][1]
                if buildtarget == target or buildtarget in targets:
                    # We've already got an attempt made, skip.
                    newbuild = True
                    break
            except:
                print('Skipping %s, no taskinfo.' % name)
                continue
        if newbuild:
            print('Skipping %s, already attempted.' % name)
            continue

        # Check out git
        fname = flavor + '/' + name
        mayduplicatecommits = False
        if os.path.exists(os.path.join(workdir, name)):
            mayduplicatecommits = True
            fedpkgcmd = ['git', 'checkout', 'master', '-q']
            #print('checking out %s master' % name)
            if runme(fedpkgcmd, 'git checkout master', name, enviro,
                os.path.join(workdir, name)):
                continue
            fedpkgcmd = ['git', 'pull', '-q']
            #print('checking out %s pull' % name)
            if runme(fedpkgcmd, 'git pull', name, enviro, os.path.join(workdir, name)):
                continue
        else:
            fedpkgcmd = ['rfpkg', 'clone', fname]
            print('checking out %s' % name)
            if runme(fedpkgcmd, 'rfpkg clone', name, enviro, workdir):
                continue

        # Check for a checkout
        if not os.path.exists(os.path.join(workdir, name)):
            print('%s failed checkout.\n' % name)
            continue

        # Check for a noautobuild file
        if os.path.exists(os.path.join(workdir, name, 'noautobuild')):
            # Maintainer does not want us to auto build.
            print('Skipping %s, due to opt-out' % name)
            continue

        # Check for dead.package file
        if os.path.exists(os.path.join(workdir, name, 'dead.package')):
            # dead.package found we should skip or we may skip safely
            print('Skipping %s, due dead.package' % name)
            continue

        # Find the spec file
        files = os.listdir(os.path.join(workdir, name))
        spec = ''
        for filename in files:
            if filename.endswith('.spec'):
                spec = filename
                break

        if not spec:
            print('%s failed spec check !\n' % name)
            continue

        # rpmdev-bumpspec
        bumpspec = ['rpmdev-bumpspec', '-D', '-u', user, '-c', comment, spec]
        print('Bumping %s' % spec)
        if runme(bumpspec, 'bumpspec', name, enviro, os.path.join(workdir, name)):
            print('bumpspec %s failed \n' % bumpspec)
            continue

        if mayduplicatecommits:
            # git commit
            commit = ['rfpkg', 'commit', '-s', '-m', comment]
        else:
            # git commit and push
            commit = ['rfpkg', 'commit', '-s', '-p', '-m', comment]
        print('Committing changes for %s' % name)
        if runme(commit, 'commit', name, enviro, os.path.join(workdir, name)):
            continue

        if mayduplicatecommits:
            print('check if %s BUMP AGAIN and run rfpkg push && rfpkg build \n' % name)
            continue

        # get git url
        urlcmd = ['rfpkg', 'giturl']
        print('Getting git url for %s' % name)
        url = runmeoutput(urlcmd, 'giturl', name, enviro, os.path.join(workdir, name))
        if not url:
            continue

        # Only submit once fewer than max_concurrent_builds of our tasks
        # are currently running; otherwise wait and re-check.
        active_tasks = wait_for_room(kojisession, active_tasks, max_concurrent_builds)

        # build
        buildcmd = ['rfpkg', 'build', '--nowait', '--background', '--fail-fast']
        print('Building %s' % name)
        output = runmeoutput(buildcmd, 'build', name, enviro, os.path.join(workdir, name))

        task_id = None
        if output:
            m = re.search(r'Created task:\s*(\d+)', output)
            if m:
                task_id = int(m.group(1))

        if task_id:
            print('%s submitted as task %s (%d/%d slots now in use)'
                  % (name, task_id, len(active_tasks) + 1, max_concurrent_builds))
            active_tasks.add(task_id)
        else:
            print('Could not parse task id for %s, build may not have been submitted. Output was:\n%s'
                  % (name, output))

    # wait for any stragglers still running once we're out of packages
    wait_for_tasks(kojisession, active_tasks)


for flavor in flavors:
    workdir = os.path.join(local_workdir, flavor)
    if not os.path.isdir(workdir):
        print('Before run this script you need set local_workdir in this script and create the directory where it will save the work, please run:\nmkdir -p %s' % workdir)

for flavor in flavors:
    workdir = os.path.join(local_workdir, flavor)
    if not os.path.isdir(workdir):
        exit(1)
    mass_rebuild(tag, workdir, flavor)
