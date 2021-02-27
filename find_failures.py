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

from __future__ import print_function
import koji
import os
import subprocess
import sys
import operator
import datetime

# Set some variables
# Some of these could arguably be passed in as args.
flavor = 'free'
flavor2= 'nonfree'
rpmfusion = 'f34-%s' % flavor
rpmfusion2 = 'f34-%s' % flavor2
buildtag = '%s-build' % rpmfusion  # tag to build from
buildtag2 = '%s-build' % rpmfusion2  # tag to build from
targets = ['%s-candidate' % rpmfusion , 'rawhide-%s' % flavor, 'rawhide-%s-multilibs' % flavor, '%s' % rpmfusion] # tag to build from
epoch = '2021-02-02 00:00:00.000000' # rebuild anything not built after this date
user = 'RPM Fusion Release Engineering <leigh123linux@gmail.com>'
comment = '- Rebuilt for https://fedoraproject.org/wiki/Fedora_33_Mass_Rebuild'
workdir = os.path.expanduser('~/rpmfusion/new/free/massrebuild')
enviro = os.environ
target = '%s' % rpmfusion
failures = {} # dict of owners to lists of packages that failed.
failed = [] # raw list of failed packages

pkg_skip_list = ['rpmfusion-free-release', 'rpmfusion-nonfree-release',
'buildsys-build-rpmfusion', 'rpmfusion-packager', 'rpmfusion-free-appstream-data', 'rpmfusion-nonfree-appstream-data',
'rfpkg-minimal', 'rfpkg', 'lpf-cleartype-fonts', 'lpf-flash-plugin', 'lpf-mscore-fonts', 'lpf-mscore-tahoma-fonts',
'lpf-spotify-client', 'mock-rpmfusion-free', 'mock-rpmfusion-nonfree', 'rpmfusion-free-obsolete-packages',
'rpmfusion-nonfree-obsolete-packages', 'rpmfusion-free-remix-kickstarts', 'rpmfusion-nonfree-remix-kickstarts',
'ufoai-data']

# Define functions

# This function needs a dry-run like option
def runme(cmd, action, pkg, env, cwd=workdir):
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
def runmeoutput(cmd, action, pkg, env, cwd=workdir):
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


# Create a koji session
kojisession = koji.ClientSession('https://koji.rpmfusion.org/kojihub')

# Generate a list of packages to iterate over
pkgs = kojisession.listPackages(buildtag, inherited=True)

# reduce the list to those that are not blocked and sort by package name
pkgs = sorted([pkg for pkg in pkgs if (not pkg['blocked'] and pkg['tag_name'] == target)],
            key=operator.itemgetter('package_name'))

print('Checking %s packages...' % len(pkgs))
"""
Keyword arguments:
kojisession -- connected koji.ClientSession instance
epoch -- string representing date to start looking for failed builds
         from. Format: "%F %T.%N"
buildtag -- tag where to look for failed builds (usually fXX-rebuild)
"""
# Get a list of failed build tasks since our epoch
failtasks = sorted(kojisession.listBuilds(createdAfter=epoch, state=3),
    key=operator.itemgetter('task_id'))

# Get a list of successful builds tagged
goodbuilds = kojisession.listTagged(buildtag, latest=True)
goodbuilds += kojisession.listTagged(buildtag2, latest=True)

# Get a list of successful builds after the epoch in our dest tag
destbuilds = kojisession.listTagged(rpmfusion, latest=True, inherit=True)
destbuilds += kojisession.listTagged(rpmfusion2, latest=True, inherit=True)
destbuilds += kojisession.listTagged(rpmfusion + "-multilibs", latest=True, inherit=True)
destbuilds += kojisession.listTagged(rpmfusion2 + "-multilibs", latest=True, inherit=True)
destbuilds += kojisession.listTagged(rpmfusion + "-updates-testing", latest=True, inherit=True)
destbuilds += kojisession.listTagged(rpmfusion2 + "-updates-testing", latest=True, inherit=True)
for build in destbuilds:
    if build['creation_time'] > epoch:
        goodbuilds.append(build)

pkgs = kojisession.listPackages(rpmfusion, inherited=True)
pkgs += kojisession.listPackages(rpmfusion2, inherited=True)
pkgs += kojisession.listPackages(rpmfusion + "-multilibs", inherited=True)
pkgs += kojisession.listPackages(rpmfusion2 + "-multilibs", inherited=True)
pkgs += kojisession.listPackages(rpmfusion + "-updates-testing", inherited=True)
pkgs += kojisession.listPackages(rpmfusion2 + "-updates-testing", inherited=True)

# get the list of packages that are blocked
pkgs = sorted([pkg for pkg in pkgs if pkg['blocked']],
              key=operator.itemgetter('package_id'))

# Check if newer build exists for package
failbuilds = []
for build in failtasks:
    if ((not build['package_id'] in [goodbuild['package_id'] for goodbuild in goodbuilds]) and (not build['package_id'] in [pkg['package_id'] for pkg in pkgs])):
        failbuilds.append(build)
        #print(build)

# Generate the dict with the failures and urls
for build in failbuilds:
    taskurl = 'https://koji.rpmfusion.org/koji/taskinfo?taskID=%s' % build['task_id']
    pkg = build['package_name']
    failures[pkg] = taskurl
    if pkg not in failed:
        failed.append(pkg)

now = datetime.datetime.now()
now_str = "%s UTC" % str(now.utcnow())
print('<html><head>')
print('<title>Packages that failed to build as of %s</title>' % now_str)
print('<style type="text/css"> dt { margin-top: 1em } </style>')
print('</head><body>')
print("<p>Last run: %s</p>" % now_str)

print('%s failed builds:<p>' % len(failed))

# Print the results
print('<dl>')
print('<style type="text/css"> dt { margin-top: 1em } </style>')
print('<dt>%s (%s):</dt>' % ("rpmfusion", len(failures)))
for pkg in sorted(failures.keys()):
    print('<dd><a href="%s">%s</a></dd>' % (failures[pkg], pkg))
print('</dl>')
print('</body>')
print('</html>')

