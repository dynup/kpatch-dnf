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


from __future__ import absolute_import
from __future__ import unicode_literals

import configparser
import os.path
import re

from dnfpluginscore import _, logger

import dnf.callback
import dnf.cli
import dnf.exceptions
import dnf.transaction

KPATCH_PLUGIN_NAME = "kpatch"
KPATCH_UPDATE_OPT = "autoupdate"

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

    aliases = ('kpatch',)
    summary = _('Toggles automatic installation of kpatch-patch packages')


    def __init__(self, cli):
        super(KpatchCmd, self).__init__(cli)
        self.cfg_file = _get_plugin_cfg_file(self.base.conf)


    @staticmethod
    def set_argparser(parser):
        parser.add_argument('action', metavar="auto|manual|status")


    def configure(self):
        demands = self.cli.demands

        demands.root_user = True
        if self.opts.action == "auto":
            demands.resolving = True
            demands.sack_activation = True
            demands.available_repos = True
        else:
            demands.resolving = False
            demands.sack_activation = False
            demands.available_repos = False


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


    def _update_plugin_cfg(self, value):
        if self.cfg_file is None:
            logger.warning("Couldn't find configuration file")
            return None

        conf = self._read_conf()
        if conf is None:
            return

        if not conf.has_section('main'):
            conf.add_section('main')
        conf.set('main', KPATCH_UPDATE_OPT, str(value))

        try:
            with open(self.cfg_file, 'w') as cfg_stream:
                conf.write(cfg_stream)
        except Exception as e:
            raise dnf.exceptions.Error(_("Failed to update conf file: {}").format(str(e)))


    def run(self):
        action = self.opts.action

        if action == "auto":
            self._install_missing_kpp_pkgs()
            self._update_plugin_cfg(True)
        elif action == "manual":
            self._update_plugin_cfg(False)
        elif action == "status":
            conf = self._read_conf()
            kp_status = "manual"
            if (conf is not None and conf.has_section('main') and
                conf.has_option('main', KPATCH_UPDATE_OPT) and
                conf.getboolean('main', KPATCH_UPDATE_OPT)):
                kp_status = "auto"
            logger.info(_("kpatch update setting: {}").format(kp_status))
        else:
            raise dnf.exceptions.Error(_("Invalid argument: {}").format(action))



class KpatchPlugin(dnf.Plugin):

    name = KPATCH_PLUGIN_NAME


    def __init__(self, base, cli):
        super(KpatchPlugin, self).__init__(base, cli)
        self._commiting = False
        self._autoupdate = False
        if cli is not None:
            cli.register_command(KpatchCmd)


    def config(self):
        parser = self.read_config(self.base.conf)
        try:
            self._autoupdate = (parser.has_section('main')
                                and parser.has_option('main', KPATCH_UPDATE_OPT)
                                and parser.getboolean('main', KPATCH_UPDATE_OPT))
        except Exception as e:
            logger.warning(_("Parsing file failed: {}").format(str(e)))


    def _commit_changes(self):
        self._commiting = True
        # Get dnf's dependency manager to resolve missing deps for added pkgs
        self.base.resolve(self.cli.demands.allow_erasing)
        self._commiting = False


    def resolved(self):
        # Calling self.base.resolve() will run this callback again
        if not self._autoupdate or self._commiting:
            return

        need_kpp_for = []
        explicit_kpp_install = []
        for tr_item in self.base.transaction:
            # It might not be safe to check tr_item.pkg.name as there might be
            # some dnf internal transaction items not linked to any pacakge.
            # Check first whether the action is a pacakge related action
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
