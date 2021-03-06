# Copyright (c) 2015 SUSE Linux GmbH.  All rights reserved.
#
# This file is part of kiwi.
#
# kiwi is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# kiwi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with kiwi.  If not, see <http://www.gnu.org/licenses/>
#
import re
import os

# project
from kiwi.command import Command
from kiwi.package_manager.base import PackageManagerBase
from kiwi.utils.rpm_database import RpmDataBase
from kiwi.path import Path
from kiwi.exceptions import KiwiRequestError


class PackageManagerZypper(PackageManagerBase):
    """
    **Implements base class for installation/deletion of
    packages and collections using zypper**

    :param list zypper_args: zypper arguments from repository runtime
        configuration
    :param dict command_env: zypper command environment from repository
        runtime configuration
    """
    def post_init(self, custom_args=None):
        """
        Post initialization method

        Store custom zypper arguments

        :param list custom_args: custom zypper arguments
        """
        self.custom_args = custom_args
        if not custom_args:
            self.custom_args = []

        runtime_config = self.repository.runtime_config()

        self.zypper_args = runtime_config['zypper_args']
        self.chroot_zypper_args = self.root_bind.move_to_root(
            self.zypper_args
        )

        self.command_env = runtime_config['command_env']
        self.chroot_command_env = dict(self.command_env)
        if 'ZYPP_CONF' in self.command_env:
            self.chroot_command_env['ZYPP_CONF'] = self.root_bind.move_to_root(
                [self.command_env['ZYPP_CONF']]
            )[0]

    def request_package(self, name):
        """
        Queue a package request

        :param str name: package name
        """
        self.package_requests.append(name)

    def request_collection(self, name):
        """
        Queue a collection request

        :param str name: zypper pattern name
        """
        self.collection_requests.append('pattern:' + name)

    def request_product(self, name):
        """
        Queue a product request

        :param str name: zypper product name
        """
        self.product_requests.append('product:' + name)

    def request_package_exclusion(self, name):
        """
        Queue a package exclusion(skip) request

        :param str name: package name
        """
        self.exclude_requests.append(name)

    def process_install_requests_bootstrap(self):
        """
        Process package install requests for bootstrap phase (no chroot)

        :return: process results in command type

        :rtype: namedtuple
        """
        command = ['zypper'] + self.zypper_args + [
            '--root', self.root_dir,
            'install', '--auto-agree-with-licenses'
        ] + self.custom_args + self._install_items()
        return Command.call(
            command, self.command_env
        )

    def process_install_requests(self):
        """
        Process package install requests for image phase (chroot)

        :return: process results in command type

        :rtype: namedtuple
        """
        if self.exclude_requests:
            # For zypper excluding a package means, removing it from
            # the solver operation. This is done by adding a package
            # lock. This means that if the package is hard required
            # by another package, it will break the transaction.
            metadata_dir = ''.join([self.root_dir, '/etc/zypp'])
            if not os.path.exists(metadata_dir):
                Path.create(metadata_dir)
            for package in self.exclude_requests:
                Command.run(
                    [
                        'chroot', self.root_dir, 'zypper'
                    ] + self.chroot_zypper_args + ['al'] + [package],
                    self.chroot_command_env
                )
        return Command.call(
            ['chroot', self.root_dir, 'zypper'] + self.chroot_zypper_args + [
                'install', '--auto-agree-with-licenses'
            ] + self.custom_args + self._install_items(),
            self.chroot_command_env
        )

    def process_delete_requests(self, force=False):
        """
        Process package delete requests (chroot)

        :param bool force: force deletion: true|false

        :raises KiwiRequestError: if none of the packages to delete is
            installed
        :return: process results in command type

        :rtype: namedtuple
        """
        delete_items = []
        for delete_item in self._delete_items():
            try:
                Command.run(['chroot', self.root_dir, 'rpm', '-q', delete_item])
                delete_items.append(delete_item)
            except Exception:
                # ignore packages which are not installed
                pass
        if not delete_items:
            raise KiwiRequestError(
                'None of the requested packages to delete are installed'
            )
        if force:
            force_options = ['--nodeps', '--allmatches', '--noscripts']
            return Command.call(
                [
                    'chroot', self.root_dir, 'rpm', '-e'
                ] + force_options + delete_items,
                self.chroot_command_env
            )
        else:
            return Command.call(
                [
                    'chroot', self.root_dir, 'zypper'
                ] + self.chroot_zypper_args + [
                    'remove', '-u', '--force-resolution'
                ] + delete_items,
                self.chroot_command_env
            )

    def update(self):
        """
        Process package update requests (chroot)

        :return: process results in command type

        :rtype: namedtuple
        """
        return Command.call(
            ['chroot', self.root_dir, 'zypper'] + self.chroot_zypper_args + [
                'update', '--auto-agree-with-licenses'
            ] + self.custom_args,
            self.chroot_command_env
        )

    def process_only_required(self):
        """
        Setup package processing only for required packages
        """
        if '--no-recommends' not in self.custom_args:
            self.custom_args.append('--no-recommends')

    def process_plus_recommended(self):
        """
        Setup package processing to also include recommended dependencies.
        """
        if '--no-recommends' in self.custom_args:
            self.custom_args.remove('--no-recommends')

    def match_package_installed(self, package_name, zypper_output):
        """
        Match expression to indicate a package has been installed

        This match for the package to be installed in the output
        of the zypper command is not 100% accurate. There might
        be false positives due to sub package names starting with
        the same base package name

        :param list package_list: list of all packages
        :param str log_line: zypper status line

        :returns: match or None if there isn't any match

        :rtype: match object, None
        """
        return re.match(
            '.*Installing: ' + re.escape(package_name) + '.*', zypper_output
        )

    def match_package_deleted(self, package_name, zypper_output):
        """
        Match expression to indicate a package has been deleted

        :param list package_list: list of all packages
        :param str log_line: zypper status line

        :returns: match or None if there isn't any match

        :rtype: match object, None
        """
        return re.match(
            '.*Removing: ' + re.escape(package_name) + '.*', zypper_output
        )

    def post_process_install_requests_bootstrap(self):
        """
        Move the rpm database to the place as it is expected by the
        rpm package installed during bootstrap phase
        """
        rpmdb = RpmDataBase(self.root_dir)
        if rpmdb.has_rpm():
            rpmdb.set_database_to_image_path()

    def has_failed(self, returncode):
        """
        Evaluate given result return code

        In zypper any return code == 0 or >= 100 is considered success.
        Any return code different from 0 and < 100 is treated as an
        error we care for. Return codes >= 100 indicates an issue
        like 'new kernel needs reboot of the system' or similar which
        we don't care in the scope of image building

        :param int returncode: return code number

        :return: True|False

        :rtype: boolean
        """
        if returncode == 0:
            # All is good
            return False
        elif returncode == 104 or returncode == 105 or returncode == 106:
            # Treat the following exit codes as error
            # 104 - ZYPPER_EXIT_INF_CAP_NOT_FOUND
            # 105 - ZYPPER_EXIT_ON_SIGNAL
            # 106 - ZYPPER_EXIT_INF_REPOS_SKIPPED
            return True
        elif returncode >= 100:
            # Treat all other 100 codes as non error codes
            return False

        # Treat any other error code as error
        return True

    def _install_items(self):
        items = self.package_requests + self.collection_requests \
            + self.product_requests
        self.cleanup_requests()
        return items

    def _delete_items(self):
        # collections and products can't be deleted
        items = []
        items += self.package_requests
        self.cleanup_requests()
        return items
