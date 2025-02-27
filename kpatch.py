# kpatch patch module packages management plugin for dnf
#
# Copyright (C) 2020 Julien Thierry <jthierry@redhat.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA,
# 02110-1301, USA.


"""
The DNF plugin helps customers to install kpatch-patch packages
when the kernel is upgraded and filter kernel-core packages that
are supported by the kpatch team.
"""


from __future__ import absolute_import
from __future__ import unicode_literals

import configparser
import os.path
import re

from dnfpluginscore import _, logger

import dnf
import dnf.callback
import dnf.cli
import dnf.exceptions
import dnf.transaction
import hawkey

KPATCH_PLUGIN_NAME = "kpatch"
KPATCH_UPDATE_OPT = "autoupdate"
KPATCH_FILTER_OPT = "autofilter"

KERNEL_PKG_NAME = "kernel-core"

# Dnf offers to lookup and read the plugin config file but doesn't provide
# a way to update that file nor to get the name...
def _get_plugin_cfg_file(base_conf):
    files = ['%s/%s.conf' % (path, KPATCH_PLUGIN_NAME) for path in base_conf.pluginconfpath]
    for file in files:
        if os.path.isfile(file):
            return file
    return None


def _kpp_name_from_kernel_pkg(kernel_pkg):
    kernel_release = re.match(r"(.*)\.el.*", kernel_pkg.release).group(1)
    kpp_kernel_release = kernel_release.replace(".", "_")
    kpp_kernel_version = kernel_pkg.version.replace(".", "_")
    return "kpatch-patch-{}-{}".format(kpp_kernel_version, kpp_kernel_release)


def _install_kpp_pkg(dnf_base, kernel_pkg):
    kpp_pkg_name = _kpp_name_from_kernel_pkg(kernel_pkg)
    kpp_pkgs_query = dnf_base.sack.query().filter(name=kpp_pkg_name,
                                                  arch=kernel_pkg.arch)
    kpp_sltr = dnf.selector.Selector(dnf_base.sack)
    kpp_sltr.set(pkg=kpp_pkgs_query.latest())
    dnf_base.goal.install(select=kpp_sltr, optional=not dnf_base.conf.strict)


class KpatchCmd(dnf.cli.Command):
    """ Extend DNF with kpatch specific commands """

    aliases = ('kpatch',)
    summary = _('Toggles automatic installation of kpatch-patch packages')


    def __init__(self, cli):
        super().__init__(cli)
        self.cfg_file = _get_plugin_cfg_file(self.base.conf)


    @staticmethod
    def set_argparser(parser):
        """
        argparse python class
        """
        parser.add_argument('action',
                            metavar="auto-update|manual-update|" \
                            "auto-filter|no-filter|install|status|" \
                            "auto|manual"
                            )


    def configure(self):
        """
        configure DemandSheet
        Collection of demands that different CLI parts have on other parts
        """
        demands = self.cli.demands

        demands.root_user = True
        if self.opts.action in ["auto-update", "install", "status", "auto"]:
            demands.resolving = True
            demands.sack_activation = True
            demands.available_repos = True
        else:
            demands.resolving = False
            demands.sack_activation = False
            demands.available_repos = False


    def _list_missing_kpp_pkgs(self):
        kpps = []

        installed_kernels = self.base.sack.query().installed().filter(name=KERNEL_PKG_NAME)

        for kernel_pkg in installed_kernels:
            kpp_pkg_name = _kpp_name_from_kernel_pkg(kernel_pkg)
            installed = self.base.sack.query().installed().filter(name=kpp_pkg_name).run()

            if installed:
                sub_q = self.base.sack.query().filter(
                            name=kpp_pkg_name,
                            release=installed[0].release,
                            version=installed[0].version
                            )
                kpp_pkgs_query = self.base.sack.query().filter(
                            name=kpp_pkg_name,
                            arch=kernel_pkg.arch
                            ).latest().difference(sub_q)
            else:
                kpp_pkgs_query = self.base.sack.query().filter(
                            name=kpp_pkg_name,
                            arch=kernel_pkg.arch
                            ).latest()

            for pkg in kpp_pkgs_query:
                kpps.append(str(pkg))

        return kpps


    def _install_missing_kpp_pkgs(self):
        installed_kernels = self.base.sack.query().installed().filter(name=KERNEL_PKG_NAME)

        for kernel_pkg in installed_kernels:
            _install_kpp_pkg(self.base, kernel_pkg)


    def _read_conf(self):
        if self.cfg_file is None:
            logger.warning("Couldn't find configuration file")
            return None
        try:
            parser = configparser.ConfigParser()
            parser.read(self.cfg_file)
            return parser
        except Exception as e:
            raise dnf.exceptions.Error(_("Parsing file failed: {}").format(str(e)))


    def _update_plugin_cfg(self, option, value):
        if self.cfg_file is None:
            logger.warning("Couldn't find configuration file")
            return

        conf = self._read_conf()
        if conf is None:
            return

        if not conf.has_section('main'):
            conf.add_section('main')
        conf.set('main', option, str(value))

        try:
            with open(self.cfg_file, 'w', encoding='utf-8') as cfg_stream:
                conf.write(cfg_stream)
        except Exception as e:
            raise dnf.exceptions.Error(_("Failed to update conf file: {}").format(str(e)))


    def run(self):
        """
        Decision tree, execution based on config
        """
        action = self.opts.action

        if action in ("auto-update", "auto"):
            self._install_missing_kpp_pkgs()
            self._update_plugin_cfg(KPATCH_UPDATE_OPT, True)
            logger.info(_("Kpatch update setting: {}").format(action))

        elif action in ("manual-update", "manual"):
            self._update_plugin_cfg(KPATCH_UPDATE_OPT, False)
            logger.info(_("Kpatch update setting: {}").format(action))

        elif action == "auto-filter":
            self._update_plugin_cfg(KPATCH_FILTER_OPT, True)
            logger.info(_("Kpatch filter setting: {}").format(action))

        elif action == "no-filter":
            self._update_plugin_cfg(KPATCH_FILTER_OPT, False)
            logger.info(_("Kpatch filter setting: {}").format(action))

        elif action == "status":
            conf = self._read_conf()
            kp_status = "manual-update"
            if (conf is not None and conf.has_section('main') and
                conf.has_option('main', KPATCH_UPDATE_OPT) and
                conf.getboolean('main', KPATCH_UPDATE_OPT)):
                kp_status = "auto-update"
            logger.info(_("Kpatch update setting: {}").format(kp_status))

            kp_status = "no-filter"
            if (conf is not None and conf.has_section('main') and
                conf.has_option('main', KPATCH_FILTER_OPT) and
                conf.getboolean('main', KPATCH_FILTER_OPT)):
                kp_status = "auto-filter"
            logger.info(_("Kpatch filter setting: {}").format(kp_status))

            kpps = self._list_missing_kpp_pkgs()
            if kpps:
                logger.info(_("Available patches: {}").format(", ".join(kpps)))

        elif action == "install":
            self._install_missing_kpp_pkgs()

        else:
            raise dnf.exceptions.Error(_("Invalid argument: {}").format(action))


class KpatchPlugin(dnf.Plugin):
    """
    The DNF plugin helps customers to install kpatch-patch packages
    when the kernel is upgraded and filter kernel-core packages that
    are supported by the kpatch team.
    """

    name = KPATCH_PLUGIN_NAME

    # list of package names to filter based on kpatch support
    kernel_pkg_names = ['kernel', 'kernel-core', 'kernel-modules',
                        'kernel-modules-core', 'kernel-modules-extra']
    kpatch_requirement = ['kernel', 'kernel-uname-r']

    def __init__(self, base, cli):
        super().__init__(base, cli)
        self._commiting = False
        self._autoupdate = False
        self._autofilter = False
        if cli is not None:
            cli.register_command(KpatchCmd)


    def config(self):
        parser = self.read_config(self.base.conf)
        try:
            self._autoupdate = (parser.has_section('main')
                                and parser.has_option('main', KPATCH_UPDATE_OPT)
                                and parser.getboolean('main', KPATCH_UPDATE_OPT))
            self._autofilter = (parser.has_section('main')
                                and parser.has_option('main', KPATCH_FILTER_OPT)
                                and parser.getboolean('main', KPATCH_FILTER_OPT))
        except Exception as e:
            logger.warning(_("Parsing file failed: {}").format(str(e)))


    def _commit_changes(self):
        self._commiting = True
        # Get dnf's dependency manager to resolve missing deps for added pkgs
        self.base.resolve(self.cli.demands.allow_erasing)
        self._commiting = False


    def sack(self):
        if not self._autofilter:
            return

        print('Please note, kpatch filter is enabled, only kpatch supported kernels are shown.')

        # This query gradually accumulates all kernel packages that should be
        # offered to the user (kernels for which exists kpatch-patch-* package
        # that requires it). Start with empty query.
        kernels_keep = self.base.sack.query().filterm(empty=True)

        # pre-filter all available versions of the kernel* packages
        kernels_query = self.base.sack.query(flags=hawkey.IGNORE_EXCLUDES)
        kernels_query.filterm(name=self.kernel_pkg_names)
        # any installed kernel version should not be excluded
        kernels_query = kernels_query.available()

        # Add to the kernels_keep query all kernel-core package versions that are
        # required by any of kpatch-patch-* packages.
        kpatch_query = self.base.sack.query(flags=hawkey.IGNORE_EXCLUDES)
        kpatch_query.filterm(name__glob="kpatch-patch-*")
        for kpatch_pkg in kpatch_query:
            for require in kpatch_pkg.requires:
                require_parsed = str(require).split(' ')
                if len(require_parsed) < 3:
                    continue
                if require_parsed[0] in self.kpatch_requirement:
                    # get kernel-core package providing "kernel-uname-r = <kpatch_pkg.evra>"
                    kernel_core = kernels_query.filter(provides=require)
                    kernel_evr = None
                    for kernel_core_pkg in kernel_core:
                        # assume that all such packages have the same evr
                        kernel_evr = kernel_core_pkg.evr
                        break
                    if kernel_evr is not None:
                        kernels_keep = kernels_keep.union(kernels_query.filter(evr=kernel_evr))
                    # assume the is only one kernel-uname-r requirement
                    break

        # exclude all kernel-core packages that are not in kernels_keep query
        self.base.sack.add_excludes(kernels_query.difference(kernels_keep))

    def resolved(self):
        # Calling self.base.resolve() will run this callback again
        if not self._autoupdate or self._commiting:
            return

        need_kpp_for = []
        explicit_kpp_install = []
        for tr_item in self.base.transaction:
            # It might not be safe to check tr_item.pkg.name as there might be
            # some dnf internal transaction items not linked to any package.
            # Check first whether the action is a package related action
            if tr_item.action in dnf.transaction.FORWARD_ACTIONS:
                if tr_item.pkg.name == KERNEL_PKG_NAME:
                    need_kpp_for.append(tr_item.pkg)
                elif tr_item.pkg.name.startswith("kpatch-patch-"):
                    explicit_kpp_install.append(tr_item.pkg.name)

        # If the user already requested a kpatch-patch package, don't override it
        # nor conflict with it
        need_kpp_for = [pkg for pkg in need_kpp_for
                        if _kpp_name_from_kernel_pkg(pkg) not in explicit_kpp_install]
        for kernel_pkg in need_kpp_for:
            _install_kpp_pkg(self.base, kernel_pkg)

        if need_kpp_for:
            self._commit_changes()
