#!/usr/bin/env python

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
# Copyright: 2017 IBM
# Author: Pridhiviraj Paidipeddi <ppaidipe@linux.vnet.ibm.com>
# this script run IO stress on nic devices for give time.

import os
import re
import time
try:
    import pxssh
except ImportError:
    from pexpect import pxssh

from avocado import Test
from avocado.utils import distro
from avocado import main
from avocado.utils import process
from avocado.utils.process import CmdError


class CommandFailed(Exception):
    def __init__(self, command, output, exitcode):
        self.command = command
        self.output = output
        self.exitcode = exitcode

    def __str__(self):
        return "Command '%s' exited with %d.\nOutput:\n%s" \
               % (self.command, self.exitcode, self.output)


class HtxNicTest(Test):

    """
    HTX [Hardware Test eXecutive] is a test tool suite. The goal of HTX is to
    stress test the system by exercising all hardware components concurrently
    in order to uncover any hardware design flaws and hardware hardware or
    hardware-software interaction issues.
    :see:https://github.com/open-power/HTX.git
    :param mdt_file: mdt file used to trigger HTX
    :params time_limit: how much time(hours) you want to run this stress.
    :param host_public_ip: Public IP address of host
    :param peer_public_ip: Public IP address of peer
    :param peer_password: password of peer for peer_user user
    :param peer_user: User name of Peer
    :param host_interfaces: Host N/W Interface's to run HTX on
    :param peer_interfaces: Peer N/W Interface's to run HTX on
    :param net_ids: Net id's of N/W Interface's
    """

    def setUp(self):
        """
        Build 'HTX'.
        """
        if 'ppc64' not in process.system_output('uname -a', ignore_status=True,
                                                shell=True, sudo=True):
            self.cancel("Platform does not support HTX tests")

        self.parameters()
        self.host_distro = distro.detect()
        self.login(self.peer_ip, self.peer_user, self.peer_password)
        self.get_ips()
        self.get_peer_distro()
        # Currently test assumes HTX is installed on both Host & Peer
        # TODO: Clone HTX & build it
        cmd = "test -d /usr/lpp/htx/"
        try:
            process.run(cmd, shell=True, sudo=True)
        except CmdError:
            self.cancel("HTX is not installed on Host")
        try:
            self.run_command(cmd)
        except CommandFailed:
            self.cancel("HTX is not installed on Peer")

    def parameters(self):
        self.host_ip = self.params.get("host_public_ip", '*', default=None)
        self.peer_ip = self.params.get("peer_public_ip", '*', default=None)
        self.peer_user = self.params.get("peer_user", '*', default=None)
        self.peer_password = self.params.get("peer_password",
                                             '*', default=None)
        self.host_intfs = self.params.get("host_interfaces",
                                          '*', default=None).split(",")
        self.peer_intfs = self.params.get("peer_interfaces",
                                          '*', default=None).split(",")
        self.net_ids = self.params.get("net_ids", '*', default=None).split(",")
        self.mdt_file = self.params.get("mdt_file", '*', default="net.mdt")
        self.time_limit = int(self.params.get("time_limit",
                                              '*', default=2)) * 3600
        self.query_cmd = "htxcmdline -query -mdt %s" % self.mdt_file

    def login(self, ip, username, password):
        '''
        SSH Login method for remote server
        '''
        pxh = pxssh.pxssh()
        # Work-around for old pxssh not having options= parameter
        pxh.SSH_OPTS = "%s  -o 'StrictHostKeyChecking=no'" % pxh.SSH_OPTS
        pxh.SSH_OPTS = "%s  -o 'UserKnownHostsFile /dev/null' " % pxh.SSH_OPTS
        pxh.force_password = True

        pxh.login(ip, username, password)
        pxh.sendline()
        pxh.prompt(timeout=60)
        pxh.sendline('exec bash --norc --noprofile')
        pxh.prompt(timeout=60)
        # Ubuntu likes to be "helpful" and alias grep to
        # include color, which isn't helpful at all. So let's
        # go back to absolutely no messing around with the shell
        pxh.set_unique_prompt()
        pxh.prompt(timeout=60)
        self.pxssh = pxh

    def run_command(self, command, timeout=300):
        '''
        SSH Run command method for running commands on remote server
        '''
        self.log.info("Running the command on peer lpar %s", command)
        if not hasattr(self, 'pxssh'):
            self.fail("SSH Console setup is not yet done")
        con = self.pxssh
        con.sendline(command)
        con.expect("\n")  # from us
        con.expect(con.PROMPT, timeout=timeout)
        output = con.before.splitlines()
        con.sendline("echo $?")
        con.prompt(timeout)
        exitcode = int(''.join(con.before.splitlines()[1:]))
        if exitcode != 0:
            raise CommandFailed(command, output, exitcode)
        return output

    def get_ips(self):
        self.host_ips = {}
        for intf in self.host_intfs:
            cmd = "ip addr list %s |grep 'inet ' |cut -d' ' -f6| \
                  cut -d/ -f1" % intf
            ip = process.system_output(cmd, ignore_status=True,
                                       shell=True, sudo=True)
            self.host_ips[intf] = ip
        self.peer_ips = {}
        for intf in self.peer_intfs:
            cmd = "ip addr list %s |grep 'inet ' |cut -d' ' -f6| \
                  cut -d/ -f1" % intf
            ip = self.run_command(cmd)[-1]
            self.peer_ips[intf] = ip

    def get_peer_distro(self):
        res = self.run_command("cat /etc/os-release")
        output = "\n".join(res)
        if "ubuntu" in output:
            self.peer_distro = "Ubuntu"
        elif "rhel" in output:
            self.peer_distro = "rhel"
        elif "sles" in output:
            self.peer_distro = "SuSE"
        else:
            self.fail("Unknown peer distro type")
        self.log.info("Peer distro is %s", self.peer_distro)

    def test(self):
        """
        This test will be in two phases
        Phase 1: Configure all necessary pre-setup steps for both the
                 interfaces in both Host & Peer
        Phase 2: Start the HTX setup & execution of test for a time_limit
                 Monitor HTX error log for any errors in both Host & Peer
        """
        self.setup_htx_nic()
        self.run_htx()

    def setup_htx_nic(self):
        self.update_host_peer_names()
        self.generate_bpt_file()
        self.check_bpt_file_existence()
        self.update_otherids_in_bpt()
        self.update_net_ids_in_bpt()
        self.htx_configure_net()

    def update_host_peer_names(self):
        """
        Update hostname & ip of both Host & Peer in /etc/hosts file of both
        Host & Peer
        """
        host_name = process.system_output("hostname", ignore_status=True,
                                          shell=True, sudo=True)
        peer_name = self.run_command("hostname")[-1]
        hosts_file = '/etc/hosts'
        self.log.info("Updating hostname of both Host & Peer in \
                      %s file", hosts_file)
        with open(hosts_file, 'r') as file:
            filedata = file.read()
        search_str1 = "%s.* %s" % (host_name, self.host_ip)
        search_str2 = "%s.* %s" % (peer_name, self.peer_ip)
        add_str1 = "%s %s" % (host_name, self.host_ip)
        add_str2 = "%s %s" % (peer_name, self.peer_ip)
        obj = re.search(search_str1, filedata)
        if not obj:
            filedata = "%s\n%s" % (add_str1, filedata)

        obj = re.search(search_str2, filedata)
        if not obj:
            filedata = "%s\n%s" % (add_str2, filedata)

        with open(hosts_file, 'w') as file:
            for line in filedata:
                file.write(line)

        filedata = self.run_command("cat %s" % hosts_file)
        for line in filedata:
            obj = re.search(search_str1, line)
            if obj:
                break
        else:
            filedata.append(add_str1)

        for line in filedata:
            obj = re.search(search_str2, line)
            if obj:
                break
        else:
            filedata.append(add_str2)
        filedata = "\n".join(filedata)
        self.run_command("echo \'%s\' > %s" % (filedata, hosts_file))

    def generate_bpt_file(self):
        """
        Generates bpt file in both Host & Peer
        """
        self.log.info("Generating bpt file in both Host & Peer")
        cmd = "echo n | /usr/bin/build_net help"
        self.run_command(cmd)
        try:
            process.run(cmd, shell=True, sudo=True)
        except CmdError as details:
            self.fail("Command %s failed %s" % (cmd, details))

    def check_bpt_file_existence(self):
        """
        Verifies the bpt file existence in both Host & Peer
        """
        self.bpt_file = '/usr/lpp/htx/bpt'
        cmd = "ls %s" % self.bpt_file
        res = self.run_command(cmd)
        if "No such file or directory" in "\n".join(res):
            self.fail("bpt file not generated in peer lpar")
        try:
            process.run(cmd, shell=True, sudo=True)
        except CmdError as details:
            msg = "Command %s failed %s, bpt file %s doesn't \
                  exist in host" % (cmd, details, self.bpt_file)
            self.fail(msg)

    def update_otherids_in_bpt(self):
        """
        Update host ip in peer bpt file & peer ip in host bpt file
        """
        # Update other id's in host lpar
        with open(self.bpt_file, 'r') as file:
            filedata = file.read()
        search_str1 = "other_ids=%s:" % self.host_ip
        replace_str1 = "%s%s" % (search_str1, self.peer_ip)

        filedata = re.sub(search_str1, replace_str1, filedata)
        with open(self.bpt_file, 'w') as file:
            for line in filedata:
                file.write(line)

        # Update other id's in peer lpar
        search_str2 = "other_ids=%s:" % self.peer_ip
        replace_str2 = "%s%s" % (search_str2, self.host_ip)
        filedata = self.run_command("cat %s" % self.bpt_file)
        for line in filedata:
            obj = re.search(search_str2, line)
            if obj:
                idx = filedata.index(line)
                filedata[idx] = replace_str2
                break
        else:
            self.fail("Failed to get other_ids string in peer lpar")
        filedata = "\n".join(filedata)
        self.run_command("echo \'%s\' > %s" % (filedata, self.bpt_file))

    def update_net_ids_in_bpt(self):
        """
        Update net id's in both Host & Peer bpt file for both N/W interfaces
        """
        # Update net id in host lpar
        with open(self.bpt_file, 'r') as file:
            filedata = file.read()
        for (host_intf, net_id) in zip(self.host_intfs, self.net_ids):
            search_str = "%s n" % host_intf
            replace_str = "%s %s" % (host_intf, net_id)
            filedata = re.sub(search_str, replace_str, filedata)
        with open(self.bpt_file, 'w') as file:
            for line in filedata:
                file.write(line)

        # Update net id in peer lpar
        filedata = self.run_command("cat %s" % self.bpt_file)

        for (peer_intf, net_id) in zip(self.peer_intfs, self.net_ids):
            search_str = "%s n" % peer_intf
            replace_str = "%s %s" % (peer_intf, net_id)

            for line in filedata:
                obj = re.search(search_str, line)
                if obj:
                    string = re.sub(search_str, replace_str, line)
                    idx = filedata.index(line)
                    filedata[idx] = string
                    break
            else:
                self.fail("Failed to get intf %s net_id in peer bpt file",
                          peer_intf)

        filedata = "\n".join(filedata)
        self.run_command("echo \'%s\' > %s" % (filedata, self.bpt_file))

    def htx_configure_net(self):
        self.log.info("Starting the N/W ping test for HTX in Host")
        cmd = "build_net %s" % self.bpt_file
        output = process.system_output(cmd, ignore_status=True, shell=True,
                                       sudo=True)
        # Try up to 10 times until pingum test passes
        for count in range(11):
            if count == 0:
                try:
                    output_peer = self.run_command(cmd, timeout=300)
                except CommandFailed as cf:
                    output_peer = cf.output
                    self.log.debug("Command %s failed %s", cf.command,
                                   cf.output)
            if "All networks ping Ok" not in output:
                self.run_command("systemctl restart network", timeout=300)
                process.system("systemctl restart network", shell=True,
                               ignore_status=True)
                output = process.system_output("pingum", ignore_status=True,
                                               shell=True, sudo=True)
            else:
                break
            time.sleep(30)
        else:
            self.fail("N/W ping test for HTX failed in Host(pingum)")

        self.log.info("Starting the N/W ping test for HTX in Peer")
        for count in range(11):
            if "All networks ping Ok" not in "\n".join(output_peer):
                try:
                    output_peer = self.run_command("pingum", timeout=300)
                except CommandFailed as cf:
                    output_peer = cf.output
                self.log.info("\n".join(output_peer))
            else:
                break
            time.sleep(30)
        else:
            self.fail("N/W ping test for HTX failed in Peer(pingum)")
        self.log.info("N/W ping test for HTX passed in both Host & Peer")

    def run_htx(self):
        self.start_htx_deamon()
        self.shutdown_active_mdt()
        self.select_net_mdt()
        self.query_net_devices_in_mdt()
        self.suspend_all_net_devices()
        self.activate_mdt()
        self.is_net_devices_active()
        self.start_htx_run()
        self.monitor_htx_run()

    def start_htx_deamon(self):
        cmd = '/usr/lpp/htx/etc/scripts/htxd_run'
        self.log.info("Starting the HTX Deamon in Host")
        process.run(cmd, shell=True, sudo=True)

        self.log.info("Starting the HTX Deamon in Peer")
        self.run_command(cmd)

    def select_net_mdt(self):
        self.log.info("Selecting the htx %s file in Host", self.mdt_file)
        cmd = "htxcmdline -select -mdt %s" % self.mdt_file
        process.run(cmd, shell=True, sudo=True)

        self.log.info("Selecting the htx %s file in Peer", self.mdt_file)
        self.run_command(cmd)

    def query_net_devices_in_mdt(self):
        self.is_net_devices_in_host_mdt()
        self.is_net_devices_in_peer_mdt()

    def is_net_devices_in_host_mdt(self):
        '''
        verifies the presence of given net devices in selected mdt file
        '''
        self.log.info("Checking host_interfaces presence in %s",
                      self.mdt_file)
        output = process.system_output(self.query_cmd, shell=True, sudo=True)
        absent_devices = []
        for intf in self.host_intfs:
            if intf not in output:
                absent_devices.append(intf)
        if absent_devices:
            self.log.info("net_devices %s are not avalable in host %s ",
                          absent_devices, self.mdt_file)
            self.fail("HTX fails to list host n/w interfaces")

        self.log.info("Given host net interfaces %s are available in %s",
                      self.host_intfs, self.mdt_file)

    def is_net_devices_in_peer_mdt(self):
        '''
        verifies the presence of given net devices in selected mdt file
        '''
        self.log.info("Checking peer_interfaces presence in %s",
                      self.mdt_file)
        output = self.run_command(self.query_cmd)
        output = " ".join(output)
        absent_devices = []
        for intf in self.peer_intfs:
            if intf not in output:
                absent_devices.append(intf)
        if absent_devices:
            self.log.info("net_devices %s are not avalable in peer %s ",
                          absent_devices, self.mdt_file)
            self.fail("HTX fails to list peer n/w interfaces")

        self.log.info("Given peer net interfaces %s are available in %s",
                      self.peer_intfs, self.mdt_file)

    def activate_mdt(self):
        self.log.info("Activating the N/W devices with mdt %s in Host",
                      self.mdt_file)
        cmd = "htxcmdline -activate all -mdt %s" % self.mdt_file
        try:
            process.run(cmd, shell=True, sudo=True)
        except CmdError as details:
            self.log.debug("Activation of N/W devices (%s) failed in Host",
                           self.mdt_file)
            self.fail("Command %s failed %s" % (cmd, details))

        self.log.info("Activating the N/W devices with mdt %s in Peer",
                      self.mdt_file)
        try:
            self.run_command(cmd)
        except CommandFailed as cf:
            self.log.debug("Activation of N/W devices (%s) failed in Peer",
                           self.mdt_file)
            self.fail("Command %s failed %s" % (cmd, str(cf)))

    def is_net_devices_active(self):
        if not self.is_net_device_active_in_host():
            self.fail("Net devices are failed to activate in Host \
                      after HTX activate")
        if not self.is_net_device_active_in_peer():
            self.fail("Net devices are failed to activate in Peer \
                      after HTX activate")

    def start_htx_run(self):
        self.log.info("Running the HTX for %s on Host", self.mdt_file)
        cmd = "htxcmdline -run -mdt %s" % self.mdt_file
        process.run(cmd, shell=True, sudo=True)

        self.log.info("Running the HTX for %s on Peer", self.mdt_file)
        self.run_command(cmd)

    def monitor_htx_run(self):
        for time_loop in range(0, self.time_limit, 60):
            self.log.info("Monitoring HTX Error logs in Host")
            cmd = 'htxcmdline -geterrlog'
            process.run(cmd, ignore_status=True,
                        shell=True, sudo=True)
            if os.stat('/tmp/htxerr').st_size != 0:
                self.fail("Check errorlogs for exact error/failure in host")
            self.log.info("Monitoring HTX Error logs in Peer")
            self.run_command(cmd)
            try:
                self.run_command('test -s /tmp/htxerr')
                rc = True
            except CommandFailed as cf:
                rc = False
            if rc:
                output = self.run_command("cat /tmp/htxerr")
                self.log.debug("HTX error log in peer: %s\n",
                               "\n".join(output))
                self.fail("Check errorlogs for exact error/failure in peer")
            self.log.info("Status of N/W devices after every 60 sec")
            process.system(self.query_cmd, ignore_status=True,
                           shell=True, sudo=True)
            try:
                output = self.run_command(self.query_cmd)
            except CommandFailed as cf:
                output = cf.output
                pass
            self.log.info("query o/p in peer lpar\n %s", "\n".join(output))
            time.sleep(60)

    def shutdown_active_mdt(self):
        self.log.info("Shutdown active mdt in host")
        cmd = "htxcmdline -shutdown"
        process.run(cmd, ignore_status=True, shell=True, sudo=True)
        self.log.info("Shutdown active mdt in peer")
        try:
            self.run_command(cmd)
        except CommandFailed:
            pass

    def suspend_all_net_devices(self):
        self.suspend_all_net_devices_in_host()
        self.suspend_all_net_devices_in_peer()

    def suspend_all_net_devices_in_host(self):
        '''
        Suspend the Net devices, if active.
        '''
        self.log.info("Suspending net_devices in host if any running")
        self.susp_cmd = "htxcmdline -suspend all  -mdt %s" % self.mdt_file
        process.run(self.susp_cmd, ignore_status=True, shell=True, sudo=True)

    def suspend_all_net_devices_in_peer(self):
        '''
        Suspend the Net devices, if active.
        '''
        self.log.info("Suspending net_devices in peer if any running")
        try:
            self.run_command(self.susp_cmd)
        except CommandFailed:
            pass

    def is_net_device_active_in_host(self):
        '''
        Verifies whether the net devices are active or not in host
        '''
        self.log.info("Checking whether all net_devices are active or \
                      not in host ")
        output = process.system_output(self.query_cmd, ignore_status=True,
                                       shell=True, sudo=True).split('\n')
        active_devices = []
        for line in output:
            for intf in self.host_intfs:
                if intf in line and 'ACTIVE' in line:
                    active_devices.append(intf)
        non_active_device = list(set(self.host_intfs) - set(active_devices))
        if non_active_device:
            return False
        else:
            self.log.info("Active N/W devices in Host %s", active_devices)
            return True

    def is_net_device_active_in_peer(self):
        '''
        Verifies whether the net devices are active or not in peer
        '''
        self.log.info("Checking whether all net_devices are active or \
                      not in peer")
        try:
            output = self.run_command(self.query_cmd)
        except CommandFailed as cf:
            output = cf.output
        active_devices = []
        for line in output:
            for intf in self.peer_intfs:
                if intf in line and 'ACTIVE' in line:
                    active_devices.append(intf)
        non_active_device = list(set(self.peer_intfs) - set(active_devices))
        if non_active_device:
            return False
        else:
            self.log.info("Active N/W devices in Peer %s", active_devices)
            return True

    def shutdown_htx_daemon(self):
        status_cmd = '/etc/init.d/htx.d status'
        shutdown_cmd = '/usr/lpp/htx/etc/scripts/htxd_shutdown'
        daemon_state = process.system_output(status_cmd, ignore_status=True,
                                             shell=True, sudo=True)
        if daemon_state.split(" ")[-1] == 'running':
            process.system(shutdown_cmd, ignore_status=True,
                           shell=True, sudo=True)
        try:
            output = self.run_command(status_cmd)
        except CommandFailed as cf:
            output = cf.output
        if 'running' in output[0]:
            try:
                self.run_command(shutdown_cmd)
            except CommandFailed:
                pass

    def clean_state(self):
        '''
        Suspend and Shutdown the active mdt
        '''
        if self.is_net_device_active_in_host():
            self.suspend_all_net_devices_in_host()
            self.log.info("Shutting down the %s in host", self.mdt_file)
            cmd = 'htxcmdline -shutdown -mdt %s' % self.mdt_file
            process.system(cmd, ignore_status=True, shell=True, sudo=True)

        if self.is_net_device_active_in_peer():
            self.suspend_all_net_devices_in_peer()
            self.log.info("Shutting down the %s in peer", self.mdt_file)
            try:
                self.run_command(cmd)
            except CommandFailed:
                pass

    def bring_up_host_interfaces(self):
        if self.host_distro.name == "Ubuntu":
            file_name = "/etc/network/interfaces"
            return  # TODO: For Ubuntu need to implement
        elif self.host_distro.name == "SuSE":
            base_name = "/etc/sysconfig/network/ifcfg-"
        elif self.host_distro.name in ["rhel", "fedora", "centos", "redhat"]:
            base_name = "/etc/sysconfig/network-scripts/ifcfg-"

        list = ["rhel", "fedora", "centos", "redhat", "SuSE"]
        if self.host_distro.name in list:
            for intf, ip in self.host_ips.iteritems():
                file_name = "%s%s" % (base_name, intf)
                with open(file_name, 'r') as file:
                    filedata = file.read()
                search_str = "IPADDR=.*"
                replace_str = "IPADDR=%s" % ip
                filedata = re.sub(search_str, replace_str, filedata)
                with open(file_name, 'w') as file:
                    for line in filedata:
                        file.write(line)
            cmd = "systemctl restart network"
            process.run(cmd, ignore_status=True, shell=True, sudo=True)

    def bring_up_peer_interfaces(self):
        if self.peer_distro == "Ubuntu":
            file_name = "/etc/network/interfaces"
            return  # TODO: For Ubuntu need to implement
        elif self.peer_distro == "SuSE":
            base_name = "/etc/sysconfig/network/ifcfg-"
        elif self.peer_distro in ["rhel", "fedora", "centos", "redhat"]:
            base_name = "/etc/sysconfig/network-scripts/ifcfg-"

        list = ["rhel", "fedora", "centos", "redhat", "SuSE"]
        if self.peer_distro in list:
            for intf, ip in self.peer_ips.iteritems():
                file_name = "%s%s" % (base_name, intf)
                filedata = self.run_command("cat %s" % file_name)
                search_str = "IPADDR=.*"
                replace_str = "IPADDR=%s" % ip
                for line in filedata:
                    obj = re.search(search_str, line)
                    if obj:
                        idx = filedata.index(line)
                        filedata[idx] = replace_str
                filedata = "\n".join(filedata)
                self.run_command("echo \'%s\' > %s" % (filedata, file_name))
            cmd = "systemctl restart network"
            self.run_command(cmd)

    def tearDown(self):
        self.clean_state()
        self.shutdown_htx_daemon()
        self.bring_up_host_interfaces()
        self.bring_up_peer_interfaces()


if __name__ == "__main__":
    main()
