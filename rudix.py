#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''Rudix Package Manager -- RPM ;D'''

import sys
import os
import optparse
import tempfile
import re
import gzip
import subprocess
import urllib2
import platform

from distutils.version import LooseVersion

__author__ = 'Rudá Moura <ruda.moura@gmail.com>'
__copyright__ = 'Copyright © 2005-2014 Rudix'
__credits__ = 'Rudá Moura, Leonardo Santagada'
__license__ = 'BSD'
__version__ = '2014.5.ɑ3'

Volume = os.getenv('VOLUME', '/')
Vendor = os.getenv('VENDOR', 'org.rudix.pkg')
RudixSite = os.getenv('RUDIX_SITE', 'http://rudix.org')
RudixVersion = int(os.getenv('RUDIX_VERSION', '2014'))

OSX = {'10.6': 'Snow Leopard',
       '10.7': 'Lion',
       '10.8': 'Montain Lion',
       '10.9': 'Mavericks'}
try:
    OSXVersion = platform.mac_ver()[0].rsplit('.', 1)[0]
except:
    OSXVersion = '10.9'
OSXVersion = os.getenv('OSX_VERSION', OSXVersion)

def version_compare(v1, v2):
    'Compare software version'
    ver_rel_re = re.compile('([^-]+)(?:-(\d+)$)?')
    v1, r1 = ver_rel_re.match(v1).groups()
    v2, r2 = ver_rel_re.match(v2).groups()
    v_cmp = cmp(LooseVersion(v1), LooseVersion(v2))
    # if they are in the same version, then compare the revision
    if v_cmp == 0:
        if r1 is None:
            r1 = 0
        if r2 is None:
            r2 = 0
        return cmp(int(r1), int(r2))
    else:
        return v_cmp

def normalize(name):
    'Transform package name in package-id.'
    if name.startswith(Vendor) is False:
        package_id = '%s.%s' % (Vendor, name)
    else:
        package_id = name
    return package_id

def denormalize(package_id):
    'Transform package-id in package name.'
    if package_id.startswith(Vendor):
        name = package_id[len(Vendor)+1:]
    else:
        name = package_id
    return name

def administrator(func):
    'Restrict execution to Administrator (root)'
    if os.getuid() != 0:
        def new_func(*args, **kwargs):
            print >>sys.stderr, 'This operation requires administrator (root) privileges!'
            return 2
    else:
        new_func = func
    return new_func

def communicate(args):
    'Call a process and return its output data as a list of strings.'
    proc = subprocess.Popen(args,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    return proc.communicate()[0].splitlines()

def call(args, silent=True):
    'Call a process and return its status.'
    if silent:
        with open('/dev/null') as dev_null:
            sts = subprocess.call(args, stdout=dev_null, stderr=dev_null)
    else:
        sts = subprocess.call(args)
    return True if sts == 0 else False


class Package(object):

    """Class that represents a local package."""

    def __init__(self, package_id, volume='/'):
        self.package_id = package_id
        self.volume = volume
        self.name = denormalize(self.package_id)
        self._package = None
        self._version = None
        self._instalL_date = None

    def __str__(self):
        return "Package '%s' on volume '%s'" % (self.package_id,
                                                self.volume)

    def __repr__(self):
        return "Package('%s')" % (self.package_id)

    @property
    def installed(self):
        cmd = ['pkgutil', '--volume', self.volume, '--pkg-info', self.package_id]
        return call(cmd, silent=True)

    @property
    def version(self):
        if not self._version:
            self.get_info()
        return self._version

    @property
    def install_date(self):
        if not self._install_date:
            self.get_info()
        return self._install_date

    @property
    def package(self):
        if not self._package:
            self.get_info()
        self._package = '%s-%s.pkg' % (self.name, self.version)
        return self._package

    def get_info(self):
        cmd = ['pkgutil', '-v', '--volume', self.volume,
                                '--pkg-info', self.package_id]
        out = communicate(cmd)
        version = '?'
        install_date = '?'
        for line in out:
            line = line.strip()
            if line.startswith('version: '):
                self._version = line[len('version: '):]
            if line.startswith('install-time: '):
                self._install_date = line[len('install-time: '):]
        return self._version, self._install_date

    def get_content(self, filter_dirs=True):
        cmd = ['pkgutil', '--volume', self.volume, '--files', self.package_id]
        out = communicate(cmd)
        content = [self.volume + line.strip() for line in out]
        if filter_dirs:
            content = [x for x in content if os.path.isfile(x)]
        return content

    def uninstall(self, verbose=False):
        for x in self.get_content():
            try:
                os.unlink(x)
            except OSError as err:
                print >> sys.stderr, err
            else:
                if verbose:
                    print "Removing '%s'" % x
        cmd = ['pkgutil', '--volume', self.volume, '--forget', self.package_id]
        return call(cmd, silent=False)


class RemotePackage(object):

    """Class that represents a remote package."""

    def __init__(self,
                 package,
                 site_url=RudixSite,
                 rudix_version=RudixVersion,
                 osx_version=OSXVersion):
        self.package = package
        url = '{base}/download/{rudix}/{osx}'
        self.url = url.format(base=site_url,
                              rudix=rudix_version,
                              osx=osx_version)
        self._name = None
        self._version = None
        self._revision = None

    def __str__(self):
        return "Package '%s' on '%s'" % (self.package, self.url)

    def __repr__(self):
        return "RemotePackage('%s')" % self.package

    @property
    def package_id(self):
        if self._name is None:
            self.split()
        return normalize(self._name)

    @property
    def name(self):
        if self._name is None:
            self.split()
        return self._name

    @property
    def version(self):
        if self._version is None:
            self.split()
        return '%s-%s' % (self._version, self._revision)

    def split(self):
        pat = re.compile(r'^(.+)-([^-]+)-(\d+)\.pkg$')
        self._name, self._version, self._revision = pat.match(self.package).groups()
        return self._name, self._version, self._revision

    def download(self, store_path=None, verbose=False):
        tempf = None
        if store_path is None:
            tempf, file_path = tempfile.mkstemp(suffix=self.package)
            store_path = file_path
        url = self.url + '/{package}'
        url = url.format(package=self.package)
        cmd = ['curl', url, '--output', store_path, '--remote-time',
                            '--continue-at', '-', '--location']
        if verbose:
            cmd.append('--progress-bar')
        else:
            cmd.append('--silent')
        call(cmd, silent=False)
        if tempf:
            os.close(tempf)
        return store_path


class Repository(object):

    """Class that represents a local repository."""

    def __init__(self, volume='/', vendor=Vendor):
        self.volume = volume
        self.vendor = vendor
        self.packages = []

    def __str__(self):
        return "%d packages(s) installed on volume '%s'" % (len(self.packages),
                                                            self.volume)

    def __repr__(self):
        return "Repository('%s')" % self.volume

    def sync(self):
        self.get_packages()
        return True

    def get_packages(self):
        cmd = ['pkgutil', '--volume', self.volume,
               '--pkgs=' + self.vendor + '.*']
        out = communicate(cmd)
        self.packages = [line.strip() for line in out]
        return self.packages

    def install_package(self, filename, verbose=False):
        cmd = ['installer']
        if verbose:
            cmd.append('-verbose')
        cmd.extend(['-pkg', filename, '-target', self.volume])
        call(cmd, silent=False)

    def remote_install_package(self, remote_package, verbose=False):
        path = remote_package.download(verbose=True)
        self.install_package(path, verbose)
        os.remove(path)

    def search_path(self, path):
        'Search for path in all packages'
        packages = []
        out = communicate(['pkgutil', '--file-info', path])
        for line in out:
            line = line.strip()
            if line.startswith('pkgid: '):
                packages.append(line[len('pkgid: '):])
        return packages


class RemoteRepository(object):

    """Class that represents a remote repository."""

    def __init__(self,
                 site_url=RudixSite,
                 rudix_version=RudixVersion,
                 osx_version=OSXVersion):
        self.site_url = site_url
        self.rudix_version = rudix_version
        self.osx_version = osx_version
        url = '{base}/download/{rudix}/{osx}'
        self.url = url.format(base=self.site_url,
                              rudix=self.rudix_version,
                              osx=self.osx_version)
        self.aliases = {}
        self.packages = []

    def __str__(self):
        return "%d package(s) available on '%s'" % (len(self.packages),
                                                    self.url)

    def __repr__(self):
        return "RemoteRepository('%s')" % self.url

    def _retrieve_manifest(self):
        url = self.url + '/00MANIFEST.txt'
        cmd = ['curl', '-s', url]
        content = communicate(cmd)
        if not content:
            return False
        for line in content:
            if line.endswith('.pkg'):
                self.packages.append(line)
        return True

    def sync(self):
        status = self._retrieve_manifest()
        if status == False:
            print >> sys.stderr, "Could not synchronize with '%s'" % self.site_url
            return False
        return True

    def match_package(self, pkg):
        if pkg in self.packages:
            return RemotePackage(pkg)
        else:
            return None

    def get_versions(self, name):
        versions = []
        for pkg in self.packages:
            p = RemotePackage(pkg)
            if name == p.name:
                versions.append(p)
        if versions:
            versions = sorted(list(set(versions)),
                              reverse=True,
                              cmp=lambda x, y: version_compare(x.version,
                                                               y.version))
            return versions
        else:
            return []

    def latest_version(self, name):
        versions = self.get_versions(name)
        return versions[0] if versions else []

    def search_package(self, name):
        options = []
        for pkg in self.packages:
            p = RemotePackage(pkg)
            if name == p.name:
                options.append((pkg, '%s-%s' % (v, r)))
            else:
                pass
        if options:
            options = sorted(list(set(options)),
                             cmp=lambda x, y: version_compare(x.version,
                                                              y.version))
            options = [x[0] for x in options]
            return options[-1]
        else:
            return None


def command_search(options, args=[]):
    'List all available (remote) packages.'
    sts = 0
    remote = RemoteRepository()
    if remote.sync() is False:
        return 1
    if not args:
        for pkg in remote.packages:
            print pkg
    else:
        for name in args:
            versions = remote.get_versions(name)
            if versions:
                for p in versions:
                    print p.package
            else:
                print >>sys.stderr, "No match for '%s'" % name
                sts = 1
    return sts

def command_list(options, args):
    'List all installed packages.'
    repo = Repository(options.volume)
    repo.sync()
    if not repo.packages:
        print >>sys.stderr, 'No Rudix packages installed.'
        return 1
    for pkg in repo.packages:
        pkg = normalize(pkg)
        if options.verbose:
            p = Package(pkg)
            print '%s version %s (install: %s)' % (p.package_id,
                                                   p.version,
                                                   p.install_date)
        else:
            print pkg
    return 0

def command_info(options, args=[]):
    'Show information about installed packages.'
    sts = 0
    if not args:
        repo = Repository(options.volume)
        repo.sync()
        args = repo.packages
    for pkg in args:
        pkg = normalize(pkg)
        p = Package(pkg, volume=options.volume)
        if p.installed is False:
            print >>sys.stderr, "Package '%s' is not installed" % pkg
            sts = 1
            continue
        print '---'
        print 'Name: %s' % p.name
        print 'Version: %s' % p.version
        print 'Install date: %s' % p.install_date
        if options.verbose:
            print 'Package-id: %s' % p.package_id
            print 'Package: %s' % p.package
    return sts

def command_files(options, args=[]):
    "Show package's files."
    sts = 0
    for pkg in args:
        pkg = normalize(pkg)
        p = Package(pkg, volume=options.volume)
        if p.installed is False:
            print >>sys.stderr, "Package '%s' is not installed" % pkg
            sts = 1
            continue
        for x in p.get_content(filter_dirs=not options.verbose):
            print x
    return sts

def command_download(options, args):
    'Download packages from Internet.'
    sts = 0
    repo = Repository(options.volume)
    repo.sync()
    remote = RemoteRepository()
    if not remote.sync():
        remote = None
    for name in args:
        if os.path.isfile(name):
            print "Found package '%s'" % name
            repo.install_package(name, options.verbose)
        else:
            if remote:
                pkg = remote.match_package(name) or remote.latest_version(name)
                if pkg:
                    print 'Downloading %s...' % pkg.package
                    pkg.download(store_path=pkg.package, verbose=True)
                else:
                    print >>sys.stderr, "No match for '%s'" % name
                    sts = 1
    return sts

@administrator
def command_install(options, args=[]):
    'Install packages from file system or Internet.'
    sts = 0
    repo = Repository(options.volume)
    repo.sync()
    remote = RemoteRepository()
    if not remote.sync():
        remote = None
    for name in args:
        if os.path.isfile(name):
            print "Found package '%s'" % name
            repo.install_package(name, options.verbose)
        else:
            if remote:
                pkg = remote.match_package(name) or remote.latest_version(name)
                if pkg:
                    print 'Downloading %s...' % pkg.package
                    repo.remote_install_package(pkg, options.verbose)
                else:
                    print >>sys.stderr, "No match for '%s'" % name
                    sts = 1
    return sts

@administrator
def command_update(options, args):
    'Try to update the current base of packages.'
    repo = Repository(options.volume)
    repo.sync()
    remote = RemoteRepository()
    if not remote.sync():
        return 1
    to_update = []
    for pkg in repo.packages:
        p_local = Package(pkg)
        p_remote = remote.latest_version(p_local.name)
        if version_compare(p_local.version,
                           p_remote.version) >= 0:
            continue
        print '{0:25} {1:10} will be updated to version {2}'.format(p_local.name,
                                                                    p_local.version,
                                                                    p_remote.version)
        to_update.append(p_remote)
    if not to_update:
        print 'All packages are up to date'
    else:
        total = len(to_update)
        for cnt, p in enumerate(to_update):
            print '[%d/%d] Downloading %s...' % (cnt+1, total, p.package)
            repo.remote_install_package(p, options.verbose)
    return 0

@administrator
def command_remove(options, args=[]):
    'Remove (uninstall) one or more packages.'
    sts = 0
    for pkg in args:
        pkg = normalize(pkg)
        p = Package(pkg)
        if p.installed:
            p.uninstall(options.verbose)
        else:
            if options.verbose:
                print >>sys.stderr, '%s is not installed' % p
            else:
                print >>sys.stderr, "Package '%s' is not installed" % pkg
            sts = 1
    return sts

@administrator
def command_remove_all(options, args=[]):
    'Remove (uninstall) all packages.'
    print "Using this option will remove *ALL* Rudix packages!"
    print "Are you sure you want to proceed? (answer 'yes' or 'y' to confirm)"
    answer = raw_input().strip()
    if answer not in ['yes', 'y']:
        print 'Great!'
        return
    print 'Removing packages...'
    repo = Repository(options.volume)
    repo.get_packages()
    for pkg in repo.packages:
        p = Package(pkg)
        p.uninstall(options.verbose)
    # Remember LinuxConf...
    print 'Cry a little tear, because Rudix is not on this machine anymore...'

def command_status(options, args):
    'Show repositories status.'
    print 'Rudix %d on OS X %s (%s)' % (RudixVersion,
                                        OSXVersion,
                                        OSX[OSXVersion])
    repo = Repository(options.volume)
    repo.sync()
    print repo
    if options.verbose:
        for pkg in repo.packages:
            print pkg
    remote = RemoteRepository()
    if remote.sync() is False:
        return 1
    print remote
    if options.verbose:
        for pkg in remote.packages:
            print pkg
    return 0

def command_search_path(options, args=[]):
    'Search for path in all packages'
    sts = 0
    repo = Repository(options.volume)
    repo.sync()
    for path in args:
        pkgs = repo.search_path(path)
        if pkgs:
            print '%s:' % path,
            for pkg in pkgs:
                print '%s' % pkg,
            print
        else:
            print >>sys.stderr, "No match for '%s'" % path
            sts = 1
    return sts

def command_freeze(options, args=[]):
    'Output installed packages in package file format.'
    repo = Repository(options.volume)
    repo.sync()
    for pkg in repo.packages:
        print Package(pkg).package


def main(args=None):
    if args is None:
        args = sys.argv[1:]
    usage = 'Usage: %prog command [options] [arguments]'
    version = 'Rudix Package Manager (%prog) version ' + __version__ + '\n'
    version += __copyright__
    parser = optparse.OptionParser(usage=usage,
                                   version=version)
    parser.add_option('-v', '--verbose', action='store_true', default=False,
                      help='displays more information when available')
    parser.add_option('--volume', default=Volume,
                      help='set volume to use. Default "%default"')
    commands = optparse.OptionGroup(parser,
                                    'Commands',
                                    'The Package manager commands.')
    commands.add_option('-l', '--list', action='store_const', dest='command',
                        const=command_list,
                        help='list all packages installed')
    commands.add_option('-I', '--info', '--show', action='store_const', dest='command',
                        const=command_info,
                        help='show information about installed packages')
    commands.add_option('-L', '--files', '--content', action='store_const', dest='command',
                        const=command_files,
                        help="show packages's files")
    commands.add_option('-i', '--install', action='store_const', dest='command',
                        const=command_install,
                        help='install local or remote package(s)')
    commands.add_option('-d', '--download', action='store_const', dest='command',
                        const=command_download,
                        help='download package(s) but do not install')
    commands.add_option('-u', '--update', '--upgrade', action='store_const', dest='command',
                        const=command_update,
                        help='update all packages')
    commands.add_option('-r', '--remove', '--uninstall', action='store_const', dest='command',
                        const=command_remove,
                        help='remove (uninstall) package(s)')
    commands.add_option('-R', '--remove-all', '--uninstall-all', action='store_const', dest='command',
                        const=command_remove_all,
                        help='remove (uninstall) ALL packages')
    commands.add_option('-t', '--status', action='store_const', dest='command',
                        const=command_status,
                        help='show repository status')
    commands.add_option('-s', '--search', action='store_const', dest='command',
                        const=command_search,
                        help='search for remote packages')
    commands.add_option('-S', '--search-path', action='store_const', dest='command',
                        const=command_search_path,
                        help='search for path in all packages and print if matched')
    commands.add_option('-z', '--freeze', action='store_const', dest='command',
                        const=command_freeze,
                        help='freeze package list.')
    parser.add_option_group(commands)
    parser.set_defaults(command=command_list)
    # Allow commands without dashes
    if args:
        command = args[0]
        if command.startswith('-') is False:
            args[0] = '--' + command
    (options, args) = parser.parse_args(args)
    return options.command(options, args)

if __name__ == '__main__':
    sys.exit(main())

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
