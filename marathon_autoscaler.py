"""Marathon auto scale module
"""
import argparse
import json
import json.tool
import logging
import math
import os
import sys
import time
from marathon import MarathonClient
import requests
import concurrent.futures
import csv
import statistics
from collections import OrderedDict

# Disable InsecureRequestWarning
from requests.packages.urllib3.exceptions import InsecureRequestWarning  # pylint: disable=F0401
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)  # pylint: disable=E1101

MARATHON_AGENT_PORT = ':5051'
PARALLEL_SLAVE_REQS = 5

# Generating CPU stress:
# yes > /dev/null &
# tail -f /dev/null

# Simulating memory usage
# for i in $(seq 5); do BLOB=$(dd if=/dev/urandom bs=1MB count=14); sleep 3s;\
# echo "iteration $i"; done


# pylint: disable=too-many-instance-attributes
class Autoscaler:
    """Marathon auto scaler
    upon initialization, it reads a list of command line parameters or env
    variables. Then it logs in to Marathon and starts querying metrics relevant
    to the scaling objective (cpu,mem). Scaling can happen by cpu, mem,
    cpu and mem, cpu or mem. The checks are performed on a configurable
    interval.
    """

    def __init__(self):
        """Initialize the object with data from the command line or environment
        variables. Connect to Marathon. Set up logging according to the verbosity requested.
        """
        self.app_instances = 0
        self.trigger_var = 0
        self.cool_down = 0
        self.cpu_util_cache = {}  # (task, host) => last cpu utilization counter

        self.parse_arguments()
        # Start logging
        if self.verbose:
            level = logging.DEBUG
        else:
            level = logging.INFO

        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.log = logging.getLogger("marathon-autoscaler")

        self.log.info("Connecting to marathon at '%s'" % self.marathon_master)
        self.client = MarathonClient(self.marathon_master, verify=False,
                                     username=self.marathon_username, password=self.marathon_password)

        # Metrics
        self.marathon_error_counter = 0
        self.mesos_error_counter = 0

    # pylint: disable=too-many-branches
    # pylint: disable=too-many-statements
    def autoscale(self, app_avg_cpu, app_avg_mem):
        """Check the marathon_app's average cpu and or memeory usage and make decision
        about scaling up or down
        Args:
            app_avg_cpu(float): The average cpu utilization across all tasks for marathon_app
            app_avg_mem(float): The average memory utilization across all tasks for marathon_app
        """
        if self.trigger_mode == "and":
            if ((self.min_cpu_time <= app_avg_cpu <= self.max_cpu_time)
                    and (self.min_mem_percent <= app_avg_mem <= self.max_mem_percent)):
                self.log.info("CPU and Memory within thresholds")
                self.trigger_var = 0
                self.cool_down = 0
            elif ((app_avg_cpu > self.max_cpu_time) and (app_avg_mem > self.max_mem_percent)
                  and (self.trigger_var >= self.trigger_number)):
                self.log.info("Autoscale triggered based on Mem and CPU exceeding threshold")
                self.scale_app(True)
            elif ((app_avg_cpu < self.max_cpu_time) and (app_avg_mem < self.max_mem_percent)
                  and (self.cool_down >= self.cool_down_factor)):
                self.log.info("Autoscale triggered based on Mem and CPU below the threshold")
                self.scale_app(False)
            elif (app_avg_cpu > self.max_cpu_time) and (app_avg_mem > self.max_mem_percent):
                self.trigger_var += 1
                self.cool_down = 0
                self.log.info(("Limits exceeded but waiting for trigger_number"
                               " to be exceeded too to scale up %s of %s"),
                              self.trigger_var, self.trigger_number)
            elif ((app_avg_cpu < self.max_cpu_time) and (app_avg_mem < self.max_mem_percent)
                  and (self.cool_down < self.cool_down_factor)):
                self.cool_down += 1
                self.trigger_var = 0
                self.log.info(("Limits are not exceeded but waiting for "
                               "cool_down to be exceeded too to scale "
                               "down %s of %s"),
                              self.cool_down, self.cool_down_factor)
            else:
                self.log.info("Mem and CPU usage exceeding thresholds")
        elif self.trigger_mode == "or":
            if ((self.min_cpu_time <= app_avg_cpu <= self.max_cpu_time)
                    and (self.min_mem_percent <= app_avg_mem <= self.max_mem_percent)):
                self.log.info("CPU or Memory within thresholds")
                self.trigger_var = 0
                self.cool_down = 0
            elif (((app_avg_cpu > self.max_cpu_time) or (app_avg_mem > self.max_mem_percent))
                  and (self.trigger_var >= self.trigger_number)):
                self.log.info("Autoscale triggered based Mem or CPU exceeding threshold")
                self.scale_app(True)
            elif (((app_avg_cpu < self.max_cpu_time) or (app_avg_mem < self.max_mem_percent))
                  and (self.cool_down >= self.cool_down_factor)):
                self.log.info("Autoscale triggered based on Mem or CPU under the threshold")
                self.scale_app(False)
            elif (app_avg_cpu > self.max_cpu_time) or (app_avg_mem > self.max_mem_percent):
                self.trigger_var += 1
                self.cool_down = 0
                self.log.info(("Mem or CPU limits exceeded but waiting for "
                               "trigger_number to be exceeded too to scale up %s of %s"),
                              self.trigger_var, self.trigger_number)
            elif (app_avg_cpu < self.max_cpu_time) or (app_avg_mem < self.max_mem_percent):
                self.cool_down += 1
                self.trigger_var = 0
                self.log.info(("Mem or CPU limits are not exceeded but waiting for "
                               "cool_down to be exceeded too to scale down %s of %s"),
                              self.cool_down, self.cool_down_factor)
            else:
                self.log.info("Mem or CPU usage not exceeding thresholds")
        elif self.trigger_mode == "cpu":
            if self.min_cpu_time <= app_avg_cpu <= self.max_cpu_time:
                self.log.info("CPU within thresholds")
                self.trigger_var = 0
                self.cool_down = 0
            elif (app_avg_cpu > self.max_cpu_time) and (self.trigger_var >= self.trigger_number):
                self.log.info("Autoscale triggered based on CPU exceeding threshold")
                self.scale_app(True)
            elif (app_avg_cpu < self.max_cpu_time) and (self.cool_down >= self.cool_down_factor):
                self.log.info("Autoscale triggered based on CPU under the threshold")
                self.scale_app(False)
            elif app_avg_cpu > self.max_cpu_time:
                self.trigger_var += 1
                self.cool_down = 0
                self.log.info(("CPU limits exceeded but waiting for "
                               "trigger_number to be exceeded too to scale "
                               "up %s of %s"),
                              self.trigger_var, self.trigger_number)
            elif app_avg_cpu < self.max_cpu_time:
                self.cool_down += 1
                self.trigger_var = 0
                self.log.info(("CPU limits are not exceeded but waiting for "
                               "cool_down to be exceeded too to scale down %s of %s"),
                              self.cool_down, self.cool_down_factor)
            else:
                self.log.info("CPU usage not exceeding threshold")
        elif self.trigger_mode == "mem":
            if self.min_mem_percent <= app_avg_mem <= self.max_mem_percent:
                self.log.info("Memory within thresholds")
                self.trigger_var = 0
                self.cool_down = 0
            elif ((app_avg_mem > self.max_mem_percent) and
                  (self.trigger_var >= self.trigger_number)):
                self.log.info("Autoscale triggered based Mem exceeding threshold")
                self.scale_app(True)
            elif ((app_avg_mem < self.max_mem_percent) and
                  (self.cool_down >= self.cool_down_factor)):
                self.log.info("Autoscale triggered based on Mem below the threshold")
                self.scale_app(False)
            elif app_avg_mem > self.max_mem_percent:
                self.trigger_var += 1
                self.cool_down = 0
                self.log.info(("Mem limits exceeded but waiting for "
                               "trigger_number to be exceeded too to scale "
                               "up %s of %s"),
                              self.trigger_var, self.trigger_number)
            elif app_avg_mem < self.max_mem_percent:
                self.cool_down += 1
                self.trigger_var = 0
                self.log.info(("Mem limits are not exceeded but waiting for "
                               "cool_down to be exceeded too to scale down"
                               " %s of %s"),
                              self.cool_down, self.cool_down_factor)
            else:
                self.log.info("Mem usage not exceeding threshold")

    def scale_app(self, is_up):
        """Scale marathon_app up or down
        Args:
            is_up(bool): Scale up if True, scale down if False
        """
        if is_up:
            target_instances = math.ceil(self.app_instances * self.autoscale_multiplier)
            if target_instances > self.max_instances:
                self.log.info("Reached the set maximum of instances %s", self.max_instances)
                target_instances = self.max_instances
        else:
            target_instances = math.floor(self.app_instances / self.autoscale_multiplier)
            if target_instances < self.min_instances:
                self.log.info("Reached the set minimum of instances %s", self.min_instances)
                target_instances = self.min_instances

        if self.app_instances != target_instances:
            self.log.info("scale_app: app_instances=%s target_instances=%s", self.app_instances, target_instances)
            if not self.dry_run:
                try:
                    response = self.client.scale_app(self.marathon_app, instances=target_instances)
                except Exception as e:
                    self.log.error("Failed to scale app: %s" % e)
                    self.marathon_error_counter += 1
                else:
                    self.log.debug("scale_app %s", response)
                    if is_up:
                        self.trigger_var = 0
                    else:
                        self.cool_down = 0

    def get_app_details(self):
        """Retrieve metadata about marathon_app
        Returns:
            Dictionary of task_id mapped to mesos slave url
        """
        app = self.client.get_app(self.marathon_app)
        if len(app.tasks) == 0:
            self.log.error('No task data in marathon for app %s', self.marathon_app)
        else:
            self.app_instances = app.instances
            self.log.debug("Marathon app %s has %s deployed instances",
                           self.marathon_app, self.app_instances)
            app_task_dict = {}
            for t in app.tasks:
                taskid = t.id
                hostid = t.host
                slave_id = t.slave_id
                self.log.debug("Task %s is running on host %s with slaveId %s", taskid, hostid, slave_id)
                app_task_dict[str(taskid)] = str(hostid)

            return app_task_dict

    def get_all_apps(self):
        """Query marathon for a list of its apps
        Returns:
            a list of all marathon apps
        """
        apps = self.client.list_apps()
        if len(apps) == 0:
            self.log.error("No Apps found on Marathon")
            sys.exit(1)
        else:
            ids = list(map(lambda a: a.id.strip('/'), apps))
            return ids

    def parse_arguments(self):
        """Set up an argument parser
        Override values of command line arguments with environment variables.
        """
        parser = argparse.ArgumentParser(description='Marathon autoscale app.')
        parser.set_defaults()
        parser.add_argument('--marathon-master',
                            help=('The DNS hostname or IP of your Marathon'
                                  ' Instance; remember to set MARATHON_USERNAME'
                                  ' and MARATHON_PASSWORD to enable authentication'),
                            **self.env_or_req('AS_marathon_master'))
        parser.add_argument('--max_mem_percent', type=float,
                            help=('The Max percent of Mem Usage averaged '
                                  'across all Application Instances to trigger'
                                  ' Autoscale (ie. 80)'),
                            **self.env_or_req('AS_MAX_MEM_PERCENT'))
        parser.add_argument('--max_cpu_time', type=float,
                            help=('The max percent of CPU Usage averaged across'
                                  ' all Application Instances to trigger '
                                  'Autoscale (ie. 80)'),
                            **self.env_or_req('AS_MAX_CPU_TIME'))
        parser.add_argument('--min_mem_percent', type=float,
                            help=('The min percent of Mem Usage averaged across'
                                  ' all Application Instances to trigger '
                                  'Autoscale (ie. 55)'),
                            **self.env_or_req('AS_MIN_MEM_PERCENT'))
        parser.add_argument('--min_cpu_time', type=float,
                            help=('The min percent of CPU Usage averaged across'
                                  ' all Application Instances to trigger '
                                  'Autoscale (ie. 50)'),
                            **self.env_or_req('AS_MIN_CPU_TIME'))
        parser.add_argument('--trigger_mode',
                            help=('Which metric(s) to trigger Autoscale '
                                  '(and, or, cpu, mem)'),
                            **self.env_or_req('AS_TRIGGER_MODE'))
        parser.add_argument('--autoscale_multiplier', type=float,
                            help=('Autoscale multiplier for triggered '
                                  'Autoscale (ie 2)'),
                            **self.env_or_req('AS_AUTOSCALE_MULTIPLIER'))
        parser.add_argument('--max_instances', type=int,
                            help=('The Max instances that should ever exist'
                                  ' for this application (ie. 20)'),
                            **self.env_or_req('AS_MAX_INSTANCES'))
        parser.add_argument('--marathon-app',
                            help=('Marathon Application Name to Configure '
                                  'Autoscale for from the Marathon UI'),
                            **self.env_or_req('AS_MARATHON_APP'))
        parser.add_argument('--min_instances', type=int,
                            help='Minimum number of instances to maintain',
                            **self.env_or_req('AS_MIN_INSTANCES'))
        parser.add_argument('--cool-down-factor', type=int,
                            help='Number of cycles to avoid scaling again',
                            **self.env_or_req('AS_COOL_DOWN_FACTOR'))
        parser.add_argument('--trigger_number', type=int,
                            help='Number of cycles to avoid scaling again',
                            **self.env_or_req('AS_TRIGGER_NUMBER'))
        parser.add_argument('--interval', type=int,
                            help=('Time in seconds to wait between '
                                  'checks (ie. 20)'),
                            **self.env_or_req('AS_INTERVAL'))
        parser.add_argument('-v', '--verbose', action="store_true", default=False,
                            help='Display DEBUG messages')
        parser.add_argument('--dry-run', action="store_true", default=False,
                            help="Monitor & calculate, but don't actually autocale")
        parser.add_argument('--csv-file',
                            help="The name of the file to write CSV results data")
        try:
            args = parser.parse_args()
        except argparse.ArgumentError as arg_err:
            sys.stderr.write(arg_err)
            parser.print_help()
            sys.exit(1)

        self.marathon_master = args.marathon_master
        self.max_mem_percent = float(args.max_mem_percent)
        self.min_mem_percent = float(args.min_mem_percent)
        self.max_cpu_time = float(args.max_cpu_time)
        self.min_cpu_time = float(args.min_cpu_time)
        self.trigger_mode = args.trigger_mode
        self.autoscale_multiplier = float(args.autoscale_multiplier)
        self.max_instances = float(args.max_instances)
        self.marathon_app = args.marathon_app
        self.min_instances = float(args.min_instances)
        self.cool_down_factor = float(args.cool_down_factor)
        self.trigger_number = float(args.trigger_number)
        self.interval = args.interval
        self.verbose = args.verbose
        self.dry_run = args.dry_run
        self.csv_file = args.csv_file
        self.marathon_username = os.environ['MARATHON_USERNAME']
        self.marathon_password = os.environ['MARATHON_PASSWORD']

        if self.csv_file:
            f = open(self.csv_file, 'w', newline='', buffering=1)
            self.csv = csv.writer(f, delimiter=' ', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            header = ["timestamp", "marathon_errors", "mesos_errors", "instances", "min_instances", "max_instances",
                      "min_cpu", "max_cpu", "min_mem", "max_mem"]
            for r in ["cpu", "mem"]:
                for m in ["mean", "median", "median_low", "median_high", "median_grouped", "pstdev", "pvariance",
                          "stdev", "variance"]:
                    header += [r + '_' + m]
            self.csv.writerow(header)

    def get_task_slave_stats(self, task, host):
        """ Get the performance Metrics for all the tasks for the marathon
        app specified by connecting to the Mesos Agent and then making a
        REST call against Mesos statistics
        Args:
            task: marathon app task
            host: host on which the task is running
        Returns:
            statistics for the specific task
        """

        self.log.debug("Connecting to %s", host)
        response = requests.get('http://' + host + MARATHON_AGENT_PORT + '/monitor/statistics.json')
        response.raise_for_status()
        for i in response.json():
            executor_id = i['executor_id']
            if executor_id == task:
                task_stats = i['statistics']
                self.log.debug("stats for task %s on host %s: %s", executor_id, host, task_stats)
                return task_stats

    def get_cpu_usage(self, task_stats, task, host):
        """Compute the cpu usage for a given task and slave within the sampled window.

        Returns:
            the % of CPU utilization since last time the cpu metrics were fetch for the same task/host.
        """
        if task_stats is not None:
            cpus_system_time_secs_now = float(task_stats['cpus_system_time_secs'])
            cpus_user_time_secs_now = float(task_stats['cpus_user_time_secs'])
            cpus_time_total_now = cpus_system_time_secs_now + cpus_user_time_secs_now
            timestamp_now = float(task_stats['timestamp'])
            cpus_limit = float(task_stats['cpus_limit'])
        else:
            raise Exception("Could not fetch stats from %s" % host)

        if (task, host) not in self.cpu_util_cache:
            self.cpu_util_cache[(task, host)] = (cpus_time_total_now, timestamp_now)
            return

        cpus_time_total_prev, timestamp_prev = self.cpu_util_cache[(task, host)]
        self.cpu_util_cache[(task, host)] = cpus_time_total_now, timestamp_now

        cpus_time_delta = cpus_time_total_now - cpus_time_total_prev
        timestamp_delta = timestamp_now - timestamp_prev

        if timestamp_delta == 0:
            raise Exception("timestamp_delta for task %s host %s is 0" % (task, host))

        cpu_usage = float(cpus_time_delta / timestamp_delta / cpus_limit) * 100
        return cpu_usage

    def get_mem_usage(self, task_stats, task, host):
        """Calculate memory usage for a given task and slave

        Returns:
            the percentage of RSS memory used versus the limit.
        """
        # RAM usage
        if task_stats is not None:
            mem_rss_bytes = int(task_stats['mem_rss_bytes'])
            mem_limit_bytes = int(task_stats['mem_limit_bytes'])
            if mem_limit_bytes == 0:
                self.log.error("mem_limit_bytes for task %s slave %s is 0",
                               task, host)
                return -1.0

            mem_utilization = 100 * (float(mem_rss_bytes) / float(mem_limit_bytes))

        else:
            raise Exception("Could not fetch stats from %s" % host)

        self.log.debug("task %s mem_rss_bytes %s mem_utilization %s mem_limit_bytes %s",
                       task, mem_rss_bytes, mem_utilization, mem_limit_bytes)
        return mem_utilization

    def timer(self):
        """Simple timer function
        """
        self.log.debug("Completed a cycle, sleeping for %s seconds ", self.interval)
        time.sleep(self.interval)

    @staticmethod
    def env_or_req(key):
        """Environment variable substitute
        Args:
            key (str): Name of environment variable to look for
        Returns:
            string to be included in parameter parsing configuration
        """
        if os.environ.get(key):
            result = {'default': os.environ.get(key)}
        else:
            result = {'required': True}
        return result

    def get_task_metrics_from_slave(self, task, host):
        self.log.info("Inspecting task %s on slave %s", task, host)

        task_stats = self.get_task_slave_stats(task, host)
        cpu_usage = self.get_cpu_usage(task_stats, task, host)
        mem_utilization = self.get_mem_usage(task_stats, task, host)

        self.log.debug("Resource usage for task %s on slave %s is CPU:%.2f MEM:%.2f",
                        task, host, cpu_usage if cpu_usage else -1, mem_utilization if mem_utilization else -1)
        return cpu_usage, mem_utilization

    def write_csv_line(self, app_cpu_stats, app_mem_stats, instances):
        self.log.debug("Writing CSV log line")
        self.csv.writerow(
            [int(time.time()), self.marathon_error_counter, self.mesos_error_counter, instances, self.min_instances,
             self.max_instances, self.min_cpu_time, self.max_cpu_time,
             self.min_mem_percent, self.max_mem_percent]
            + list(app_cpu_stats.values()) + list(app_mem_stats.values()))

    def calculate_stats(self, data):
        return OrderedDict([
            ("mean", statistics.mean(data)),
            ("median", statistics.median(data)),
            ("median_low", statistics.median_low(data)),
            ("median_high", statistics.median_high(data)),
            ("median_grouped", statistics.median_grouped(data)),
            ("pstdev", statistics.pstdev(data)),
            ("pvariance", statistics.pvariance(data)),
            ("stdev", statistics.stdev(data) if len(data) > 1 else 'NA'),
            ("variance", statistics.variance(data) if len(data) > 1 else 'NA')
        ])

    def run(self):
        """Main function
        Runs the query - compute - act cycle
        """
        running = 1
        self.cool_down = 0
        self.trigger_var = 0
        while running == 1:
            try:
                marathon_apps = self.get_all_apps()
            except Exception as e:
                self.log.error("Failed to get app list: %s" % e)
                self.marathon_error_counter += 1
                self.timer()
                continue

            # Quick sanity check to test for apps existence in Marathon.
            if self.marathon_app not in marathon_apps:
                self.log.error("Could not find %s", self.marathon_app)
                self.timer()
                continue

            # Get a dictionary of app taskId and hostId for the marathon app
            try:
                app_task_dict = self.get_app_details()
            except Exception as e:
                self.log.error("Failed to get app details: %s" % e)
                self.marathon_error_counter += 1
                self.timer()
                continue

            self.log.debug("Tasks for %s : %s", self.marathon_app, app_task_dict)

            app_cpu_values = []
            app_mem_values = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_SLAVE_REQS) as executor:
                futures = {executor.submit(self.get_task_metrics_from_slave, task, host): host for task, host in
                           app_task_dict.items()}

                for f in concurrent.futures.as_completed(futures):
                    host = futures[f]
                    try:
                        cpu_usage, mem_utilization = f.result()
                    except Exception as exc:
                        self.log.error('Fetching metrics from %s generated an exception: %s' % (host, exc))
                        self.mesos_error_counter += 1
                        continue
                    if cpu_usage is None or mem_utilization is None:
                        continue  # Ignore this host in this iteration, next time it will be ok
                    app_cpu_values.append(cpu_usage)
                    app_mem_values.append(mem_utilization)

            if len(app_cpu_values) == 0 or len(app_mem_values) == 0:
                self.log.info('Ignoring results of first iteration')
                self.timer()
                continue

            app_cpu_stats = self.calculate_stats(app_cpu_values)
            app_mem_stats = self.calculate_stats(app_mem_values)

            app_avg_cpu = app_cpu_stats['mean']
            self.log.info("Current average CPU time for app %s = %.2f",
                          self.marathon_app, app_avg_cpu)
            app_avg_mem = app_mem_stats['mean']
            self.log.info("Current Average Mem Utilization for app %s = %.2f",
                          self.marathon_app, app_avg_mem)

            if self.csv_file:
                self.write_csv_line(app_cpu_stats, app_mem_stats, self.app_instances)

            # Evaluate whether an autoscale trigger is called for
            self.autoscale(app_avg_cpu, app_avg_mem)
            self.timer()


if __name__ == "__main__":
    AS = Autoscaler()
    AS.run()
