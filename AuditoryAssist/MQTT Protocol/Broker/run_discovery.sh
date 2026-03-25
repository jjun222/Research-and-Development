#!/bin/bash
# MQTT 브로커 discovery UDP 서버 실행 스크립트

# (필요하면 stress 이런 건 없음)

# conda 환경 활성화
source ~/miniforge3/bin/activate mqtt-env

# discovery 서버 실행
python /home/mqtt/MQTTpr/discovery_server.py
