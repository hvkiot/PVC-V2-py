from hardware.pam import PAMController
import time

pam = PAMController()
while True:
    print(pam.cmd("RC:910"))
    time.sleep(1)
