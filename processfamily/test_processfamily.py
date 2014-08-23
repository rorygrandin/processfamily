__author__ = 'matth'

import unittest
import sys
from processfamily.test import ParentProcess, Config
import os
import subprocess
import requests
import time
import socket
import logging
import glob
from processfamily.processes import process_exists, kill_process
from processfamily import _traceback_str
import signal

class _BaseProcessFamilyFunkyWebServerTestSuite(unittest.TestCase):

    skip_crash_test = None

    def setUp(self):
        self.pid_dir = os.path.join(os.path.dirname(__file__), 'test', 'pid')
        if not os.path.exists(self.pid_dir):
            os.makedirs(self.pid_dir)
        for pid_file in self.get_pid_files():
            with open(pid_file, "r") as f:
                pid = f.read().strip()
            if pid and process_exists(int(pid)):
                logging.warning(
                    ("Process with pid %s is stilling running. This could be a problem " + \
                    "(but it might be a new process with a recycled pid so I'm not killing it).") % pid )
            else:
                os.remove(pid_file)
        self.check_server_ports_unbound()

    def tearDown(self):
        self.wait_for_parent_to_stop(5)

        #Now check that no processes are left over:
        start_time = time.time()
        processes_left_running = []
        for pid_file in self.get_pid_files():
            with open(pid_file, "r") as f:
                pid = f.read().strip()
            if pid:
                while process_exists(int(pid)) and time.time() - start_time < 5:
                    time.sleep(0.3)
                if process_exists(int(pid)):
                    processes_left_running.append(int(pid))
            os.remove(pid_file)

        for pid in processes_left_running:
            try:
                kill_process(pid)
            except Exception as e:
                logging.warning("Error killing process with pid %d: %s", pid, _traceback_str())

        start_time = time.time()
        for pid in processes_left_running:
            while process_exists(int(pid)) and time.time() - start_time < 40:
                time.sleep(0.3)

        self.check_server_ports_unbound()
        self.assertFalse(processes_left_running, msg="There should have been no PIDs left running but there were: %s" % (', '.join([str(p) for p in processes_left_running])))


    def start_up(self, wait_for_start=True):
        self.start_parent_process()
        #Wait up to 10 secs for the all ports to be available:
        start_time = time.time()
        still_waiting = True
        while still_waiting and time.time() - start_time < 10:
            still_waiting = False
            for i in range(4):
                try:
                    s = socket.socket()
                    try:
                        s.connect(("localhost", Config.get_starting_port_nr()+i))
                    except socket.error, e:
                        still_waiting = True
                        break
                finally:
                    s.close()
            if still_waiting:
                time.sleep(0.3)
        self.assertFalse(still_waiting, "Waited 10 seconds and some http ports are still not accessible")


    def get_pid_files(self):
        return glob.glob(os.path.join(self.pid_dir, "*.pid"))

    def kill_parent(self):
        for pid_file in self.get_pid_files():
            if os.path.basename(pid_file).startswith('c'):
                continue
            with open(pid_file, "r") as f:
                pid = f.read().strip()
            kill_process(int(pid))

    def test_parent_stop(self):
        self.start_up()
        self.send_parent_http_command("stop")

    def test_parent_exit(self):
        self.start_up()
        self.send_parent_http_command("exit")

    def test_parent_crash(self):
        if self.skip_crash_test:
            self.skipTest(self.skip_crash_test)
        self.start_up()
        self.send_parent_http_command("crash")

    def test_parent_interrupt_main(self):
        self.start_up()
        self.send_parent_http_command("interrupt_main")

    def test_parent_kill(self):
        self.start_up()
        self.kill_parent()

    def test_parent_stop_child_locked_up(self):
        self.start_up()
        self.freeze_up_middle_child()
        self.send_parent_http_command("stop")
        #This needs time to wait for the child for 10 seconds:
        self.wait_for_parent_to_stop(11)

    def test_parent_exit_child_locked_up(self):
        self.start_up()
        self.freeze_up_middle_child()
        self.send_parent_http_command("exit")

    def test_parent_crash_child_locked_up(self):
        if self.skip_crash_test:
            self.skipTest(self.skip_crash_test)
        self.start_up()
        self.freeze_up_middle_child()
        self.send_parent_http_command("crash")

    def test_parent_interrupt_main_child_locked_up(self):
        self.start_up()
        self.freeze_up_middle_child()
        self.send_parent_http_command("interrupt_main")
        #This needs time to wait for the child for 10 seconds:
        self.wait_for_parent_to_stop(11)

    def test_parent_kill_child_locked_up(self):
        self.start_up()
        self.freeze_up_middle_child()
        self.kill_parent()

    def test_parent_exit_child_locked_up(self):
        self.start_up()
        self.freeze_up_middle_child()
        self.send_parent_http_command("exit")

    if not sys.platform.startswith('win'):
        def test_sigint(self):
            self.skipTest("These are a bit dicey - they depend on which thread gets the signal")
            self.start_up()
            os.kill(self.parent_process.pid, signal.SIGINT)

        def test_sigint_child_locked_up(self):
            self.skipTest("These are a bit dicey - they depend on which thread gets the signal")
            self.start_up()
            os.kill(self.parent_process.pid, signal.SIGINT)
            self.freeze_up_middle_child()
            #This needs time to wait for the child for 10 seconds:
            self.wait_for_parent_to_stop(11)


    def freeze_up_middle_child(self):
        #First check that we can do this fast (i.e. things aren't stuttering because of environment):
        for i in range(5):
            self.send_middle_child_http_command("", timeout=4)
        self.send_middle_child_http_command("hold_gil_%d" % (60*10)) #Freeze up for 10 minutes
        while True:
            #Try and do this request until it takes longer than 4 secs - this would mean that we have successfully got stuck
            try:
                self.send_middle_child_http_command("", timeout=4)
            except requests.exceptions.Timeout as t:
                break

    def check_server_ports_unbound(self):
        bound_ports = []
        for pnumber in range(4):
            port = Config.get_starting_port_nr() + pnumber
            #I just try and bind to the server port and see if I have a problem:
            logging.info("Checking for ability to bind to port %d", port)
            serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if not sys.platform.startswith('win'):
                    #On linux I need this setting cos we are starting and stopping things
                    #so frequently that they are still in a STOP_WAIT state when I get here
                    serversocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                serversocket.bind(("", port))
            except Exception as e:
                bound_ports.append(port)
            finally:
                serversocket.close()
        self.assertFalse(bound_ports, "The following ports are still bound: %s" % ', '.join([str(p) for p in bound_ports]))

    def get_path_to_ParentProcessPy(self):
        return os.path.join(os.path.dirname(__file__), 'test', 'ParentProcess.py')

    def send_parent_http_command(self, command, **kwargs):
        return self.send_http_command(Config.get_starting_port_nr(), command, **kwargs)

    def send_middle_child_http_command(self, command, **kwargs):
        return self.send_http_command(Config.get_starting_port_nr()+2, command, **kwargs)

    def send_http_command(self, port, command, **kwargs):
        r = requests.get('http://localhost:%d/%s' % (port, command), **kwargs)
        return r.json

    def wait_for_process_to_stop(self, process, timeout):
        if process is None:
            return
        start_time = time.time()
        while time.time()-start_time < timeout:
            if self.parent_process.poll() is None:
                time.sleep(0.3)


class NormalSubprocessTests(_BaseProcessFamilyFunkyWebServerTestSuite):

    skip_crash_test = "The crash test throws up a dialog in this context" if sys.platform.startswith('win') else None

    def start_parent_process(self):
        self.parent_process = subprocess.Popen(
            [sys.executable, self.get_path_to_ParentProcessPy()],
            close_fds=True)

    def wait_for_parent_to_stop(self, timeout):
        self.wait_for_process_to_stop(getattr(self, 'parent_process', None), timeout)

if sys.platform.startswith('win'):
    import win32service
    import win32serviceutil

    class PythonWTests(_BaseProcessFamilyFunkyWebServerTestSuite):

        #TODO: it seems that when I'm not closing file descriptors below, the crash dialog starts popping up again:
        skip_crash_test = "The crash test throws up a dialog in this context" if sys.platform.startswith('win') else None

        def start_parent_process(self):
            self.parent_process = subprocess.Popen(
                [Config.pythonw_exe, self.get_path_to_ParentProcessPy()],
                close_fds=False) #TODO: if I close file descriptors here, then my parent process isn't able to read
                                 #  it's children's out and err streams - this is a bit mysterious

        def wait_for_parent_to_stop(self, timeout):
            self.wait_for_process_to_stop(getattr(self, 'parent_process', None), timeout)

    class WindowsServiceTests(_BaseProcessFamilyFunkyWebServerTestSuite):

        def start_parent_process(self):
            win32serviceutil.StartService(Config.svc_name)

        def wait_for_parent_to_stop(self, timeout):
            start_time = time.time()
            while time.time()-start_time < timeout:
                if win32serviceutil.QueryServiceStatus(Config.svc_name)[1] != win32service.SERVICE_STOPPED:
                    time.sleep(0.3)

        def test_parent_interrupt_main(self):
            self.skipTest("Interrupt main doesn't do anything useful in a windows service")

        def test_parent_interrupt_main_child_locked_up(self):
            self.skipTest("Interrupt main doesn't do anything useful in a windows service")

        def test_service_stop(self):
            self.start_up()
            win32serviceutil.StopService(Config.svc_name)

        def test_service_stop_child_locked_up(self):
            self.start_up()
            self.freeze_up_middle_child()
            win32serviceutil.StopService(Config.svc_name)
            #This needs time to wait for the child for 10 seconds:
            self.wait_for_parent_to_stop(11)


#Remove the base class from the module dict so it isn't smelled out by nose:
del(_BaseProcessFamilyFunkyWebServerTestSuite)