#!/bin/bash

source /opt/ros/humble/setup.bash
source /home/dlgyals/Pros2/ros2_ws/install/setup.bash

exec ros2 run mqtt_bridge mqtt_to_ros_node
