from gpiozero import Button
from signal import pause

# GPIO17 = 물리핀 11번
button = Button(17, pull_up=True, bounce_time=0.05)

def pressed():
    print("버튼 눌림")

def released():
    print("버튼 뗌")

button.when_pressed = pressed
button.when_released = released

print("아케이드 버튼 테스트 시작")
print("종료하려면 Ctrl + C")

pause()
