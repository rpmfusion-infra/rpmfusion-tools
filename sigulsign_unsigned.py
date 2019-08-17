#!/usr/bin/python
#
# sigulsign_unsigned.py - A utility to use sigul to sign rpms in koji
#
# Copyright (C) 2009-2017 Red Hat, Inc.
# SPDX-License-Identifier:      GPL-2.0
#
# Authors:
#     Jesse Keating <jkeating@redhat.com>
#     Patrick Uiterwijk <puiterwijk@redhat.com>
#
# This program requires koji and sigul installed, as well as configured.

import argparse
import os
import sys
import getpass
import subprocess
import logging
import koji
import cccolutils

errors = {}

status = 0
rpmdict = {}
unsigned = []
# Setup a dict of our key names as sigul knows them to the actual key ID
# that koji would use. This information can also be obtained using
# SigulHelper() instances

# Should probably set these from a koji config file
SERVERCA = os.path.expanduser('~/.rpmfusion-server-ca.cert')
CLIENTCA = os.path.expanduser('~/.rpmfusion-upload-ca.cert')
CLIENTCERT = os.path.expanduser('~/.rpmfusion.cert')

KEYS = {
    'rpmfusion-cuda-2019': {'id': 'c8d47bb7', 'v3': True},
    'rpmfusion-fedora-free-28': {'id': '09eab3f2', 'v3': True},
    'rpmfusion-fedora-free-29': {'id': '42f19ed0', 'v3': True},
    'rpmfusion-fedora-free-30': {'id': 'c0aeda6e', 'v3': True},
    'rpmfusion-fedora-free-31': {'id': 'c481937a', 'v3': True},
    'rpmfusion-fedora-nonfree-28': {'id': '7f858107', 'v3': True},
    'rpmfusion-fedora-nonfree-29': {'id': 'd6841af8', 'v3': True},
    'rpmfusion-fedora-nonfree-30': {'id': '1d14a795', 'v3': True},
    'rpmfusion-fedora-nonfree-31': {'id': '54a86092', 'v3': True},
    'rpmfusion-fedora-nonfree-32': {'id': '6dc1be18', 'v3': True},
    'rpmfusion-el-free-6': {'id': '849c449f', 'v3': True},
    'rpmfusion-el-free-7': {'id': 'f5cf6c1e', 'v3': True},
    'rpmfusion-el-free-8': {'id': '158b3811', 'v3': True},
    'rpmfusion-el-nonfree-6': {'id': '5568bbb2', 'v3': True},
    'rpmfusion-el-nonfree-7': {'id': 'a3108f6c', 'v3': True},
    'rpmfusion-el-nonfree-8': {'id': 'bdda8475', 'v3': True},
}


def get_gpg_agent_passphrase(cache_id, ask=False, error_message="X",
                             prompt="X", description=None):
    gpg_agent = subprocess.Popen(
        ["gpg-connect-agent"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    cmdline = ["GET_PASSPHRASE"]
    if not ask:
        cmdline.append("--no-ask")

    if not description:
        description = "Please enter password for " + cache_id
        description = description.replace(" ", "+")

    cmdline.append(cache_id)
    cmdline.append(error_message)
    cmdline.append(prompt)
    cmdline.append(description)
    cmdline.append("\n")
    cmdline = " ".join(cmdline)

    response, error = gpg_agent.communicate(cmdline)
    if gpg_agent.returncode != 0:
        raise RuntimeError("gpg-agent-connect error: {0}:{1}".format(
            gpg_agent.returncode, error))
    elif not response.startswith("OK"):
        raise RuntimeError("gpg-agent-connect error message: {0}".format(
            response))
    else:
        passphrase = response[3:-1].decode("hex")
        return passphrase
    return None


class KojiHelper(object):
    def __init__(self, arch=None):
        if arch:
            self.kojihub = arch
        else:
            self.kojihub = "koji"
        self.koji_module = koji.get_profile_module(self.kojihub)
        self.kojisession = self.koji_module.ClientSession(self.koji_module.config.server, {'krb_rdns': False})
        self.kojisession.ssl_login(CLIENTCERT, CLIENTCA, SERVERCA)

    def listTagged(self, tag, inherit=False):
        """ Return list of SRPM NVRs for a tag
        """
        builds = [build['nvr'] for build in
                  self.kojisession.listTagged(tag, latest=True,
                                              inherit=inherit)
                 ]
        return builds

    def get_build_ids(self, nvrs):
        """
        Get build ids for a list of SRPM NVRs
        """
        errors = []

        build_ids = []
        self.kojisession.multicall = True

        for build in nvrs:
            # use strict for now to traceback on bad buildNVRs
            self.kojisession.getBuild(build, strict=True)

        for build, result in zip(nvrs, self.kojisession.multiCall()):
            if isinstance(result, list):
                build_ids.append(result[0]["id"])
            else:
                errors.append(build)
        return build_ids, errors

    def get_rpms(self, build_ids):
        """ Get dict of filenames -> RPM ID for a list of build IDs
        """

        res = {}
        self.kojisession.multicall = True
        if isinstance(build_ids, int):
            build_ids = [build_ids]

        for bID in build_ids:
            self.kojisession.listRPMs(buildID=bID)
        results = self.kojisession.multiCall()
        for [rpms] in results:
            for rpm in rpms:
                filename = "{rpm[nvr]}.{rpm[arch]}.rpm".format(rpm=rpm)
                res[filename] = rpm['id']
        return res

    def get_unsigned_rpms(self, rpms, keyid):
        """ Reduce RPMs to RPMs that are not signed with keyid

            :parameter:rpms: dict RPM filename -> rpm ID
            :returns: dict: RPM filename -> rpm ID
        """
        unsigned = {}
        self.kojisession.multicall = True

        rpm_filenames = rpms.keys()
        for rpm in rpm_filenames:
            self.kojisession.queryRPMSigs(rpm_id=rpms[rpm], sigkey=keyid)

        results = self.kojisession.multiCall()
        for ([result], rpm) in zip(results, rpm_filenames):
            if not result:
                unsigned[rpm] = rpms[rpm]
        return unsigned

    def write_signed_rpms(self, rpms, keyid):
        self.kojisession.multicall = True
        rpm_filenames = list(rpms)
        for rpm in rpm_filenames:
            self.kojisession.writeSignedRPM(rpm, keyid)
        results = self.kojisession.multiCall()
        errors = {}

        for result, rpm in zip(results, rpm_filenames):
            if isinstance(result, dict):
                errors[rpm] = result
            elif result != [None]:
                raise ValueError("Unexpected Koji result: " + repr(result))
        return errors

    def check_build_is_tagged(self, build_id, tag):
        build = self.kojisession.getBuild(build_id)
        if build:
            package_name = build["name"]
            builds = self.kojisession.listTagged(tag, package=package_name)
            build_ids = [b["build_id"] for b in builds]
            if build_id in build_ids:
                return True
        return False


def exit(status):
    """End the program using status, report any errors"""

    if errors:
        for type in errors.keys():
            logging.error('Errors during %s:' % type)
            for fault in errors[type]:
                logging.error('     ' + fault)

    sys.exit(status)


# Throw out some functions
def writeRPMs(status, kojihelper, batch=None):
    """Use the global rpmdict to write out rpms within.
       Returns status, increased by one in case of failure"""

    # Check to see if we want to write all, or just the unsigned.
    if args.write_all:
        rpms = rpmdict.keys()
    else:
        if batch is None:
            rpms = [rpm for rpm in rpmdict.keys() if rpm in unsigned]
        else:
            rpms = batch
    logging.info('Calling koji to write %s rpms' % len(rpms))
    status = status
    written = 0
    rpmcount = len(rpms)
    while rpms:
        workset = rpms[0:100]
        rpms = rpms[100:]

        for rpm in workset:
            written += 1
            logging.debug('Writing out %s with %s, %s of %s',
                          rpm, key, written, rpmcount)
        errors = kojihelper.write_signed_rpms(workset, KEYS[key]['id'])

        for rpm, result in errors.items():
            logging.error('Error writing out %s' % rpm)
            errors.setdefault('Writing', []).append(rpm)
            if result['traceback']:
                logging.error('    ' + result['traceback'][-1])
            status += 1
    return status


class SigulHelper(object):
    def __init__(self, key, password=None, config_file=None, arch=None,
                 ask_with_agent=False, ask=False, use_staging=False):
        """ If password is None, ask for it

        """
        self.key = key

        if password is None:
            try:
                krb_realm = "RPMFUSION.ORG"
                if use_staging:
                    krb_realm = "STAGING.RPMFUSION.ORG"

                fas_username = cccolutils.get_user_for_realm(krb_realm)
            except:
                fas_username = getpass.getuser()

            cache_id = "sigul:{0}:{1}".format(fas_username, key)
            try:
                self.password = get_gpg_agent_passphrase(cache_id,
                                                         ask=ask_with_agent)
            except:
                self.password = None

            if self.password is None and ask and not ask_with_agent:
                self.password = getpass.getpass(
                    prompt='Passphrase for %s: ' % key)
        else:
            self.password = password
        if self.password is None:
            raise ValueError("Missing password")
        self.config_file = config_file
        self.arch = arch

        ret, pubkey, stderr = self.get_public_key()
        if ret != 0:
            raise ValueError("Invalid key or password: " + stderr)
        self.keyid = KEYS[key]['id']
        self.v3 = KEYS[key]['v3']

    def get_public_key(self):
        command = self.build_cmdline('get-public-key', self.key)
        ret, stdout, stderr = self.run_command(command)
        return ret, stdout, stderr

    def build_cmdline(self, *args):
        cmdline = ['sigul', '--batch']
        if self.config_file:
            cmdline.extend(["--config-file", self.config_file])
        cmdline.extend(args)
        return cmdline

    def run_command(self, command, eat_output=True):
        if eat_output:
            child = subprocess.Popen(command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        else:
            child = subprocess.Popen(command, stdin=subprocess.PIPE)

        stdout, stderr = child.communicate(self.password + '\0')
        ret = child.wait()
        return ret, stdout, stderr

    def build_sign_cmdline(self, rpms, arch=None):
        if arch is None:
            arch = self.arch

        if len(rpms) == 1:
            sigul_cmd = "sign-rpm"
        else:
            sigul_cmd = "sign-rpms"

        command = self.build_cmdline(sigul_cmd, '--store-in-koji',
                                     '--koji-only')
        if arch:
            command.extend(['-k', arch])

        if self.v3:
            command.append('--v3-signature')
        command.append(self.key)

        return command + rpms


if __name__ == "__main__":
    # Define our usage
    usage = 'usage: %(prog)s [options] key (build1, build2)'
    # Create a parser to parse our arguments
    parser = argparse.ArgumentParser(usage=usage)
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Be verbose, specify twice for debug')
    parser.add_argument('--tag',
                        help='Koji tag to sign, use instead of listing builds')
    parser.add_argument('--inherit', action='store_true', default=False,
                        help='Use tag inheritance to find builds.')
    parser.add_argument('--just-write', action='store_true', default=False,
                        help='Just write out signed copies of the rpms')
    parser.add_argument('--just-sign', action='store_true', default=False,
                        help='Just sign and import the rpms')
    parser.add_argument('--just-list', action='store_true', default=False,
                        help='Just list the unsigned rpms')
    parser.add_argument('--write-all', action='store_true', default=False,
                        help='Write every rpm, not just unsigned')
    parser.add_argument('--password',
                        help='Password for the key')
    parser.add_argument('--batch-mode', action="store_true", default=False,
                        help='Read null-byte terminated password from stdin')
    parser.add_argument('--arch',
                        help='Architecture when singing secondary arches')
    parser.add_argument('--sigul-batch-size',
                        help='Amount of RPMs to sign in a sigul batch',
                        default=50, type=int)
    parser.add_argument('--sigul-config-file',
                        help='Config file to use for sigul',
                        default=None, type=str)
    parser.add_argument('--gpg-agent',
                        help='Use GPG Agent to ask for password',
                        default=False, action='store_true')
    parser.add_argument('--staging', action='store_true', default=False,
                        help='Sign packages in the staging environment')
    # Get our options and arguments
    args, extras = parser.parse_known_args()

    if args.verbose <= 0:
        loglevel = logging.WARNING
    elif args.verbose == 1:
        loglevel = logging.INFO
    else:  # options.verbose >= 2
        loglevel = logging.DEBUG

    logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s',
                        level=loglevel)

    # Check to see if we got any arguments
    if not extras:
        parser.print_help()
        sys.exit(1)

    # Check to see if we either got a tag or some builds
    if args.tag and len(extras) > 2:
        logging.error('You must provide either a tag or a build.')
        parser.print_help()
        sys.exit(1)

    key = extras[0]
    logging.debug('Using %s for key %s' % (KEYS[key]['id'], key))
    if key not in KEYS.keys():
        logging.error('Unknown key %s' % key)
        parser.print_help()
        sys.exit(1)

    # Get the passphrase for the user if we're going to sign something
    # (This code stolen from sigul client.py)
    if not (args.just_list or args.just_write):
        if args.password:
            passphrase = args.password
        elif args.batch_mode:
            passphrase = ""
            while True:
                pwchar = sys.stdin.read(1)
                if pwchar == '\0':
                    break
                elif pwchar == '':
                    raise EOFError('Incomplete password')
                else:
                    passphrase += pwchar
        else:
            # let SigulHelper ask
            passphrase = None

        try:
            sigul_helper = SigulHelper(key, passphrase,
                                       config_file=args.sigul_config_file,
                                       arch=args.arch, ask=True,
                                       ask_with_agent=args.gpg_agent,
                                       use_staging=args.staging)
        except ValueError as error:
            logging.error('Error validating passphrase for key %s: %s', key,
                          error)
            sys.exit(1)

    # setup the koji session
    logging.info('Setting up koji session')
    kojihelper = KojiHelper(arch=args.arch)
    kojisession = kojihelper.kojisession

    # Get a list of builds
    # If we have a tag option, get all the latest builds from that tag,
    # optionally using inheritance.  Otherwise take everything after the
    # key as a build.
    if args.tag is not None:
        logging.info('Getting builds from %s' % args.tag)
        builds = kojihelper.listTagged(args.tag, inherit=args.inherit)
    else:
        logging.info('Getting builds from arguments')
        builds = extras[1:]

    logging.info('Got %s builds' % len(builds))

    # sort the builds
    builds = sorted(builds)
    buildNVRs = []
    cmd_build_ids = []
    for b in builds:
        if b.isdigit():
            cmd_build_ids.append(int(b))
        else:
            buildNVRs.append(b)

    if buildNVRs != []:
        logging.info('Getting build IDs from Koji')
        build_ids, buildID_errors = kojihelper.get_build_ids(buildNVRs)
        for nvr in buildID_errors:
            logging.error('Invalid n-v-r: %s' % nvr)
            status += 1
            errors.setdefault('buildNVRs', []).append(nvr)
    else:
        build_ids = []

    build_ids.extend(cmd_build_ids)

    # now get the rpm filenames and ids from each build
    logging.info('Getting rpms from each build')
    rpmdict = kojihelper.get_rpms(build_ids)
    logging.info('Found %s rpms' % len(rpmdict))

    # Now do something with the rpms.

    # If --just-write was passed, try to write them all out
    # We try to write them all instead of worrying about which
    # are already written or not.  Calls are cheap, restarting
    # mash isn't.
    if args.just_write:
        logging.info('Just writing rpms')
        exit(writeRPMs(status, kojihelper))

    # Since we're not just writing things out, we need to figure out what needs
    # to be signed.

    # Get unsigned packages
    logging.info('Checking for unsigned rpms in koji')
    unsigned = list(kojihelper.get_unsigned_rpms(rpmdict, KEYS[key]['id']))
    for rpm in unsigned:
        logging.debug('%s is not signed with %s' % (rpm, key))

    if args.just_list:
        logging.info('Just listing rpms')
        print('\n'.join(unsigned))
        exit(status)

    # run sigul
    logging.debug('Found %s unsigned rpms' % len(unsigned))
    batchsize = args.sigul_batch_size

    def run_sigul(rpms, batchnr):
        global status
        logging.info('Signing batch %s/%s with %s rpms' % (
            batchnr, (total + batchsize - 1) / batchsize, len(rpms))
                    )
        command = sigul_helper.build_sign_cmdline(rpms)
        logging.debug('Running %s' % subprocess.list2cmdline(command))
        ret = sigul_helper.run_command(command, False)[0]
        if ret != 0:
            logging.error('Error signing %s' % (rpms))
            for rpm in rpms:
                errors.setdefault('Signing', []).append(rpm)
        status += 1

    logging.info('Signing rpms via sigul')
    total = len(unsigned)
    batchnr = 0
    rpms = []
    for rpm in unsigned:
        rpms += [rpm]
        if len(rpms) == batchsize:
            batchnr += 1
            run_sigul(rpms, batchnr)
            rpms = []

    if len(rpms) > 0:
        batchnr += 1
        run_sigul(rpms, batchnr)

    # Now that we've signed things, time to write them out, if so desired.
    if not args.just_sign:
        exit(writeRPMs(status, kojihelper))

    logging.info('All done.')
    sys.exit(status)
