#!/usr/bin/env python3

from gpiozero import Button
from signal import pause
from datetime import datetime

BUTTON_PIN = 17

button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def on_pressed():
    print(f"[{now()}] GPIO17 아케이드 버튼 눌림 / value={button.value}", flush=True)


def on_released():
    print(f"[{now()}] GPIO17 아케이드 버튼 뗌 / value={button.value}", flush=True)


print(f"[{now()}] GPIO17 버튼 감시 시작", flush=True)
print(f"[{now()}] 배선: COM=GND, NO=GPIO17", flush=True)

button.when_pressed = on_pressed
button.when_released = on_released

pause()
