#!/bin/bash
# Conda 환경 활성화 후 판단서버 실행

# CPU 부하 테스트
stress -c 4 &

# conda 환경 활성화
source ~/miniforge3/bin/activate mqtt-env

# 판단 서버 실행
python /home/mqtt/MQTTpr/MQTT_decision_server.py
