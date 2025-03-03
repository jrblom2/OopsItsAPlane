from pymavlink import mavutil
from utils import RunMode
import json
import time
import threading

geoJsonKeys = [
    '_type',
    'time_boot_ms',
    'lat',
    'lon',
    'alt',
    'relative_alt',
    'vx',
    'vy',
    'vz',
    'hdg',
    '_timestamp',
]

attJsonKeys = [
    '_type',
    'time_boot_ms',
    'roll',
    'pitch',
    'yaw',
    'rollspeed',
    'pitchspeed',
    'yawspeed',
    '_timestamp',
]


class mavlinkManager:

    def __init__(self, port, mode, timestamp, videoDuration):
        self.stopSignal = False
        # Set up connection to vehicle
        self.connection = mavutil.mavlink_connection(f'udp:localhost:{port}')

        self.runMode = mode

        # If Live, open stream file to record. If recorded, open the data.
        if mode is RunMode.LIVE:
            self.writeFile = open(f"mavdumps/mavlink_{timestamp}.json", "w")
        else:
            readFile = open(f"mavdumps/mavlink_{timestamp}.json", "r")
            self.videoDuration = videoDuration
            self.mavLines = []
            for line in readFile:
                msg = json.loads(line)
                self.mavLines.append(msg)

            # Calculate if mavdata is sifnifagantly offset from video
            drift = self.mavLines[-1]["_timestamp"] - self.mavLines[0]["_timestamp"] - self.videoDuration
            print("Drift: ", drift)

            readFile.close()

        self.lastGeo = None
        self.lastAtt = None
        self.mavPoll = threading.Thread(target=self.pollGPI)
        self.mavPoll.start()

    def shutdown(self):
        self.stopSignal = True
        self.mavPoll.join()
        if hasattr(self, 'writeFile'):
            self.writeFile.close()

    def confirmHeartbeat(self):
        print("Checking heartbeat, make sure QGC is running or it is offline mode.")
        if self.runMode is RunMode.LIVE:
            self.connection.wait_heartbeat()

    def pollGPI(self):
        i = 0
        lastMessage = None

        self.confirmHeartbeat()
        while not self.stopSignal:
            if self.runMode is RunMode.LIVE:
                msg = self.connection.recv_msg()

                if msg is not None:
                    if msg.get_type() == "GLOBAL_POSITION_INT":
                        geoDump = {key: msg.__dict__[key] for key in geoJsonKeys if key in msg.__dict__}
                        self.lastGeo = geoDump
                        self.writeFile.write(json.dumps(geoDump))
                        self.writeFile.write('\n')
                        self.writeFile.flush()
                    if msg.get_type() == "ATTITUDE":
                        attDump = {key: msg.__dict__[key] for key in attJsonKeys if key in msg.__dict__}
                        self.lastAtt = attDump
                        self.writeFile.write(json.dumps(attDump))
                        self.writeFile.write('\n')
                        self.writeFile.flush()
                    msg = None
            else:
                msg = self.mavLines[i]

                if lastMessage is not None:
                    time.sleep(msg["_timestamp"] - lastMessage["_timestamp"])
                lastMessage = msg

                if lastMessage['_type'] == "GLOBAL_POSITION_INT":
                    self.lastGeo = lastMessage
                else:
                    self.lastAtt = lastMessage

                i += 1
                if i == len(self.mavLines):
                    break

        print("closing mav stream")

    def getGEO(self):
        return self.lastGeo

    def getATT(self):
        return self.lastAtt
