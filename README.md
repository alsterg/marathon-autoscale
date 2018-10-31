# marathon-autoscale
Dockerized container autoscaler that can dynamically scale a service running on Marathon based on CPU and/or MEM utilization.

## Prerequisites
A running Mesos/Marathon cluster.
An Account on Marathon with permissions to probe for apps and scale them up or down.

## Building the Docker container

How to build the container:
    
    docker build .
    docker tag <tag-id> <docker-hub-name>:marathon-autoscale:latest
    docker push <docker-hub-name>:marathon-autoscale:latest

## Program Execution
The program accepts the following arguments:

      --marathon-master MARATHON_MASTER
                            The DNS hostname or IP of your Marathon Instance;
                            remember to set MARATHON_USERNAME and
                            MARATHON_PASSWORD to enable authentication
      --max_mem_percent MAX_MEM_PERCENT
                            The Max percent of Mem Usage averaged across all
                            Application Instances to trigger Autoscale (ie. 80)
      --max_cpu_time MAX_CPU_TIME
                            The max percent of CPU Usage averaged across all
                            Application Instances to trigger Autoscale (ie. 80)
      --min_mem_percent MIN_MEM_PERCENT
                            The min percent of Mem Usage averaged across all
                            Application Instances to trigger Autoscale (ie. 55)
      --min_cpu_time MIN_CPU_TIME
                            The min percent of CPU Usage averaged across all
                            Application Instances to trigger Autoscale (ie. 50)
      --trigger_mode TRIGGER_MODE
                            Which metric(s) to trigger Autoscale (and, or, or_and,
                            cpu, mem)
      --autoscale_multiplier AUTOSCALE_MULTIPLIER
                            Autoscale multiplier for triggered Autoscale (ie 2)
      --max_instances MAX_INSTANCES
                            The Max instances that should ever exist for this
                            application (ie. 20)
      --marathon-app MARATHON_APP
                            Marathon Application Name to Configure Autoscale for
                            from the Marathon UI
      --min_instances MIN_INSTANCES
                            Minimum number of instances to maintain
      --cool-down-factor COOL_DOWN_FACTOR
                            Number of cycles to avoid scaling again
      --trigger_number TRIGGER_NUMBER
                            Number of cycles to avoid scaling again
      --interval INTERVAL   Time in seconds to wait between checks (ie. 20)
      -v, --verbose         Display DEBUG messages
      --dry-run             Monitor & calculate, but don't actually autocale
      --csv-file CSV_FILE   The name of the file to write CSV results data

## Input parameters

Some of the command line parameters can be overwritten by environment variables:

    AS_MARATHON_APP
    AS_TRIGGER_MODE
    AS_MIN_INSTANCES
    AS_MAX_INSTANCES
    AS_MAX_CPU_TIME
    AS_MIN_CPU_TIME
    AS_MAX_MEM_PERCENT
    AS_MIN_MEM_PERCENT
    AS_COOL_DOWN_FACTOR
    AS_TRIGGER_NUMBER
    AS_INTERVAL
    AS_AUTOSCALE_MULTIPLIER

**Notes** 

For MIN_CPU_TIME and MAX_CPU_TIME is taking the number of available container cores into consideration. So an 80% value refers to all cores.
For MIN_MEM_PERCENT and MAX_MEM_PERCENT on very small containers, remember that Mesos adds 32MB to the container spec for container overhead (namespace and cgroup), so your target percentages should take that into account.  Alternatively, consider using the CPU only scaling mode for containers with very small memory footprints.

## Authentication

To authenticate to Marathon, please set the `MARATHON_USERNAME` and `MARATHON_PASSWORD` environment variables.

## Scaling Modes

#### AND 

In this mode, the system will only scale the service up or down when both CPU and Memory have been out of range for the number of cycles defined in AS_TRIGGER_NUMBER (for up) or AS_COOL_DOWN_FACTOR (for down).  Rarely used.

#### OR 

In this mode, the system will scale the service up or down when either the CPU or Memory have been out of range for the number of cycles defined in AS_TRIGGER_NUMBER (for up) or AS_COOL_DOWN_FACTOR (for down).

#### OR_AND 

In this mode, the system will scale the service up if either CPU or Memory have been above of the upper bound for the number of cycles defined in AS_TRIGGER_NUMBER, and scale down if both CPU and Memory have been below the lower bound for the number of cycles defined in AS_COOL_DOWN_FACTOR. (Recommended)

#### CPU 

In this mode, the system will scale the service up or down when the CPU has been out of range for the number of cycles defined in AS_TRIGGER_NUMBER (for up) or AS_COOL_DOWN_FACTOR (for down).

#### MEM 

In this mode, the system will scale the service up or down when the Memory has been out of range for the number of cycles defined in AS_TRIGGER_NUMBER (for up) or AS_COOL_DOWN_FACTOR (for down).



