#!/usr/bin/python3
#
# find_failures.py - A utility to find packages that failed to rebuild
#
# Copyright (C) 2009-2013 Red Hat, Inc.
# Copyright (C) 2022 Sérgio Basto <sergio@serjux.com>
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

import koji
import os
import subprocess
import sys
import operator
import datetime

import requests
PAGURE_URL = 'https://src.fedoraproject.org'
ns='rpms'

# Set some variables
# Some of these could arguably be passed in as args.
"""
Keyword arguments:
kojisession -- connected koji.ClientSession instance
epoch -- string representing date to start looking for failed builds
         from. Format: "%F %T.%N"
tag -- tag where to look for failed builds (usually fXX-rebuild)
"""
tag = 'f41'
epoch = '2024-07-25 00:00:00' # rebuild anything not built after this date
local_workdir = os.path.expanduser('~/rpmfusion/new/massrebuild/')

pkg_skip_list = ['rpmfusion-free-release', 'rpmfusion-nonfree-release', 'buildsys-build-rpmfusion',
'rpmfusion-packager', 'rpmfusion-free-appstream-data', 'rpmfusion-nonfree-appstream-data',
'rfpkg-minimal', 'rfpkg', 'lpf-cleartype-fonts', 'lpf-flash-plugin', 'lpf-mscore-fonts', 'lpf-mscore-tahoma-fonts',
'lpf-spotify-client', 'mock-rpmfusion-free', 'mock-rpmfusion-nonfree', 'rpmfusion-free-obsolete-packages',
'rpmfusion-nonfree-obsolete-packages', 'rpmfusion-free-remix-kickstarts', 'rpmfusion-nonfree-remix-kickstarts',
'ufoai-data', 'wormsofprey-data']

dead_packages = subprocess.check_output("find %s -name dead.package" % local_workdir, shell=True, text=True)
noautobuild_output_packages = subprocess.check_output("find %s -name noautobuild" % local_workdir, shell=True, text=True)


print_skipped = False
debug_enabled = False
def debug(msg):
    if debug_enabled:
        print(msg)

now_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
print('<html><head>')
print('<title>Packages that failed to build as of %s</title>' % now_str)
print('<style type="text/css"> dt { margin-top: 1em } </style>')
print('</head><body>')
debug('<pre>')

# Create a koji session
kojisession = koji.ClientSession('https://koji.rpmfusion.org/kojihub')

# Get a list of successful builds tagged
debug("listTagged packages with inherit with tag = %s-updates-candidate" % tag)
destbuilds = kojisession.listTagged("%s-free-updates-candidate" % tag, latest=True, inherit=True)
destbuilds += kojisession.listTagged("%s-free-updates-testing" % tag, latest=True, inherit=True)
destbuilds += kojisession.listTagged("%s-free-tainted" % tag, latest=True, inherit=True)
debug("destbuilds %d" % len(destbuilds))
debug("listTagged packages with inherit with tag = %s-updates-candidate" % tag)
destbuilds2 = kojisession.listTagged("%s-nonfree-updates-candidate" % tag, latest=True, inherit=True)
destbuilds2 += kojisession.listTagged("%s-nonfree-updates-testing" % tag, latest=True, inherit=True)
destbuilds2 += kojisession.listTagged("%s-nonfree-tainted" % tag, latest=True, inherit=True)
debug("destbuilds %d" % len(destbuilds2))
destbuilds += destbuilds2
debug("sum of destbuilds %d" % len(destbuilds))

goodbuilds = []
# failbuilds2 is other way to count package that doesn't have a success build since epoch, not really failed builds
failbuilds2 = []
for build in destbuilds:
    if build['creation_time'] > epoch:
        goodbuilds.append(build)
    elif (build['package_name'] not in [pkg for pkg in pkg_skip_list]):
        failbuilds2.append(build)
debug("good builds after epoch %d\n" % len(goodbuilds))

#pkgs = kojisession.listPackages(target, inherited=True)
#print("target %d" % len(pkgs))
debug("listPackages with inherit with buildtag = %s-nonfree-build" % tag)
# with inherit we just need this one !?! ...
pkgs = kojisession.listPackages("%s-nonfree-build" % tag, inherited=True)
debug("len pkgs buildtag2 %d" % len(pkgs))

blockedpkgs = sorted([pkg for pkg in pkgs if (pkg['blocked'])],
key=operator.itemgetter('package_name'))

# reduce the list to those that are not blocked and sort by package name
pkgs = sorted([pkg for pkg in pkgs if (not pkg['blocked'])],
            key=operator.itemgetter('package_name'))
debug("len pkgs buildtag2 without blocked %d" % len(pkgs))
pkgs = sorted([pkg for pkg in pkgs if (not pkg['package_name'] in pkg_skip_list)],
            key=operator.itemgetter('package_name'))
debug("len pkgs buildtag2 without pkg_skip_list %d" % len(pkgs))

# Get a list of failed build tasks since our epoch
failtasks = sorted(kojisession.listBuilds(createdAfter=epoch, state=koji.BUILD_STATES['FAILED']),
    key=operator.itemgetter('task_id'))
canceledtasks = sorted(kojisession.listBuilds(createdAfter=epoch, state=koji.BUILD_STATES['CANCELED']),
    key=operator.itemgetter('task_id'))

# Check if newer build exists for package
failbuilds = []
for build in failtasks + canceledtasks:
    if (build['package_id'] in [blockedpkg['package_id'] for blockedpkg in blockedpkgs]):
        continue
    if (not build['package_id'] in [goodbuild['package_id'] for goodbuild in goodbuilds]):
        request_tag = kojisession.getTaskRequest(build['task_id'])[1]
        if request_tag.startswith(tag) or request_tag.startswith("rawhide"):
            failbuilds.append(build)
            debug(build)

debug("len of failbuild=%d" % len(failbuilds))
debug("len of failedbuilds2=%d (alternative way to count failed builds)" % len(failbuilds2))
for build in failbuilds2:
    if (build['package_name'] not in [pkg['package_name'] for pkg in failbuilds]):
        debug("from failbuilds2 not in failbuild= %s %s" % (build['package_name'], build['creation_time']))

for build in failbuilds:
    if (build['package_name'] not in [pkg['package_name'] for pkg in failbuilds2]):
        debug("from failbuilds not in failbuilds2 %s %s" % (build['package_name'], build['creation_time']))
        if (build['package_name'] not in [pkg['package_name'] for pkg in destbuilds]):
            debug("failbuild also not in destbuild list")


# Generate the dict with the failures and urls
failed_pkgs = [] # raw list of failed packages
notbuilded_pkgs = []
failures = {} # dict of owners to lists of packages that failed.
failures2 = {} # dict of owners to lists of packages that failed.

# we may use failbuilds or failbuilds2
for build in failbuilds:
    pkg = build['package_name']
    if len( [line for line in dead_packages.splitlines() if "/%s/" % pkg in line] ):
        debug("dead package = %s" % pkg)
    else:
        failures[pkg] = 'https://koji.rpmfusion.org/koji/taskinfo?taskID=%s' % build['task_id']
        if pkg not in failed_pkgs:
            failed_pkgs.append(pkg)
        else:
            debug("pkg failed more than one time %s register this one %s" % (pkg, 'https://koji.rpmfusion.org/koji/taskinfo?taskID=%s' % build['task_id']))

# pkg not in goods builds neither failed builds
for build in pkgs:
    pkg = build['package_name']
    if (not build['package_id'] in [goodbuild['package_id'] for goodbuild in goodbuilds]
        and not build['package_id'] in [pkg['package_id'] for pkg in failbuilds]):
        if len( [line for line in noautobuild_output_packages.splitlines() if "/%s/" % pkg in line] ):
            if print_skipped:
                failures2[pkg] = "repo = %s, skipped because have noautobuild file" % build['tag_name']
        elif len( [line for line in dead_packages.splitlines() if "/%s/" % pkg in line] ):
            if print_skipped:
                failures2[pkg] = "repo = %s, skipped because have dead.package file" % build['tag_name']
        else:
            # response = requests.get(f'{PAGURE_URL}/api/0/{ns}/{pkg}').json()
            # if not 'error' in response:
            #     failures2[pkg] = 'moved to fedora ? (<a target="_blank" href="%s">%s</a>) ' % (response['full_url'], response['full_url'])
            failures2[pkg] = 'repo = %s' % build['tag_name']
            notbuilded_pkgs.append(pkg)

debug('</pre>')
print("<p>Last run: %s</p>" % now_str)
# Print the results
print('<dl>')
print('<style type="text/css"> dt { margin-top: 1em } </style>')
print('<dt>Failed builds: %d </dt>' % len(failed_pkgs))
for pkg in sorted(failures.keys()):
    print('<dd>Package: <a href="%s">%s</a></dd>' % (failures[pkg], pkg))

print('<dt>Packages not built: %d </dt>' % len(notbuilded_pkgs))
for pkg in sorted(failures2.keys()):
    print('<dd>Package: %s, %s</dd>' % (pkg, failures2[pkg]))

print('</dl>')

# if we want see the count by builds that are tagged
# just to double check
second_count = False
if second_count:
    failed_pkgs = [] # raw list of failed packages
    failures = {} # dict of owners to lists of packages that failed.

    for build in failbuilds2:
        pkg = build['package_name']
        failures[pkg] = 'https://koji.rpmfusion.org/koji/taskinfo?taskID=%s' % build['task_id']
        if pkg not in failed_pkgs:
            failed_pkgs.append(pkg)
        else:
            print ("pkg failed more than one time %s register this one %s" % (pkg, 'https://koji.rpmfusion.org/koji/taskinfo?taskID=%s' % build['task_id']))

    print('%s failed builds:<p>' % len(failed_pkgs))

    # Print the results
    print('<dl>')
    print('<style type="text/css"> dt { margin-top: 1em } </style>')
    print('<dt>%s (%s):</dt>' % ("rpmfusion", len(failures)))
    for pkg in sorted(failures.keys()):
        if failures[pkg].startswith("http"):
            print('<dd><a href="%s">%s</a></dd>' % (failures[pkg], pkg))
        else:
            print('<dd>Package: %s, %s </dd>' % (pkg, failures[pkg]))
    print('</dl>')

print('</body>')
print('</html>')

