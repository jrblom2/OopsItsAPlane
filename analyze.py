from mavlinkManager import mavlinkManager
from frameScanner import frameScanner
import time
from scipy.spatial import ConvexHull
import math
import threading
import pandas as pd
from utils import RunMode
from sklearn.cluster import DBSCAN


class analyzer:
    def __init__(self, timestamp, mode, videoStream):
        self.mode = mode
        self.positions = pd.DataFrame({'id': [], 'lat': [], 'lon': [], 'alt': [], 'time': [], 'color': [], 'type': []})
        self.hullSets = []
        self.stopSignal = False

        # ./runs/detect/train7/weights/best.pt
        self.fsInterface = frameScanner(videoStream, 'yolo11n', mode, timestamp)

        videoDuration = 0.0
        if mode is RunMode.RECORDED:
            videoDuration = self.fsInterface.duration

        self.mavlink = mavlinkManager(14445, mode, timestamp, videoDuration)

        print("Run mode is: ", mode.name)

        self.analyzeThread = threading.Thread(target=self.analyzeLoop)
        self.analyzeThread.start()

    def shutdown(self):
        self.stopSignal = True
        self.analyzeThread.join()

    def updatePositions(self, row):
        if row['id'] in self.positions['id'].values:
            self.positions.loc[self.positions['id'] == row['id'], ['lat', 'lon', 'alt', 'time']] = (
                row['lat'],
                row['lon'],
                row['alt'],
                row['time'],
            )
        else:
            self.positions = pd.concat([self.positions, pd.DataFrame([row])], ignore_index=True)

    def computeHulls(self):
        self.hullSets = []
        justCars = self.positions[self.positions['type'] == 'car']

        points = []
        for _, row in justCars.iterrows():
            points.append((row['lon'], row['lat']))

        # Need several points in a class to do anything meaningful
        if len(points) > 3:
            db = DBSCAN(eps=0.00029, min_samples=3).fit(points)

            # ML Density Based grouping
            groupedPoints = {}
            for point, label in zip(points, db.labels_):
                if label == -1:
                    continue
                if label not in groupedPoints:
                    groupedPoints[label] = []  # Create a new list for this label if not already exists
                groupedPoints[label].append(point)

            # Find hull for each group of points
            for label, subset in groupedPoints.items():
                hull = ConvexHull(subset)
                hullLines = []
                for simplex in hull.simplices:
                    hullLines.append([subset[simplex[0]], subset[simplex[1]]])
                self.hullSets.append(hullLines)

    def analyzeLoop(self):
        dataTimeout = 0
        hasStartedRecord = False
        while dataTimeout < 5 and not self.stopSignal:
            # Get camera data
            ret, frame = self.fsInterface.getFrame()

            # Where are we?
            geoMsg = self.mavlink.getGEO()
            attMsg = self.mavlink.getATT()

            if not ret or geoMsg is None or attMsg is None:
                print("No data in either frames or mav data!")
                dataTimeout += 1
                time.sleep(1)
                continue

            if not hasStartedRecord and self.mode == RunMode.LIVE:
                self.mavlink.readyToRecord = True
                self.fsInterface.readyToRecord = True
                self.fsInterface.startTime = time.time()
                print(f"Started recording at: {time.time()}")
                hasStartedRecord = True

            # frame = self.fsInterface.rotateFrame(frame, attMsg['roll'])

            doDetect = True
            if doDetect:
                trimX1 = 250
                trimX2 = 250
                trimY1 = 100
                trimY2 = 100

                frame = frame[trimY1 : self.fsInterface.height - trimY2, trimX1 : self.fsInterface.width - trimX2]
                frame, results = self.fsInterface.getIdentifiedFrame(frame)
                detectionData = results[0].summary()

                altitude = geoMsg["relative_alt"] / 1000
                planeLat = geoMsg["lat"] / 10000000
                planeLon = geoMsg["lon"] / 10000000
                planeHeading = geoMsg['hdg'] / 100
                planeTilt = attMsg['pitch']

                # Remove detections older than 0.5 sec and update plane coords
                self.positions = self.positions[self.positions['time'] > time.time() - 0.5]
                planeUpdate = {
                    "id": "Plane",
                    "lat": planeLat,
                    "lon": planeLon,
                    "alt": altitude,
                    "time": time.time(),
                    'color': 'blue',
                }
                self.updatePositions(planeUpdate)

                # Camera info
                cameraSensorW = 0.00454
                cameraSensorH = 0.00340
                cameraPixelsize = 0.00000314814
                cameraFocalLength = 0.0021
                cameraTilt = 63 * (math.pi / 180)

                maxDistance = 50  # in meters

                totalTilt = cameraTilt + planeTilt

                # Basic Ground sample distance, how far in M each pixel is
                nadirGSDH = (altitude * cameraSensorH) / (cameraFocalLength * self.fsInterface.height)
                nadirGSDW = (altitude * cameraSensorW) / (cameraFocalLength * self.fsInterface.width)

                cameraCenterX = self.fsInterface.width / 2
                cameraCenterY = self.fsInterface.height / 2

                for i, detection in enumerate(detectionData):
                    # Camera is at a tilt from the ground, so GSD needs to be scaled
                    # by relative distance. Assuming camera is level horizontally, so
                    # just need to scale tilt in camera Y direction
                    if detection["name"] == "car" or detection["name"] == "person":
                        box = detection["box"]
                        objectX = ((box["x2"] - box["x1"]) / 2) + box["x1"] + trimX1
                        objectY = ((box["y2"] - box["y1"]) / 2) + box["y1"] + trimY1

                        tanPhi = cameraPixelsize * ((objectY - cameraCenterY) / cameraFocalLength)
                        verticalPhi = math.atan(tanPhi)

                        totalAngle = totalTilt - verticalPhi

                        # sanity check if past 90 degrees
                        if totalAngle > 1.57:
                            totalAngle = 1.57

                        adjustedGSDH = nadirGSDH * (1 / math.cos(totalAngle))
                        adjustedGSDW = nadirGSDW * (1 / math.cos(totalAngle))

                        # Distance camera center is projected forward
                        offsetCenterY = math.tan(totalTilt) * altitude

                        # Positive value means shift left from camera POV
                        offsetYInPlaneFrame = (cameraCenterX - objectX) * adjustedGSDW

                        # Positive value means shift up in camera POV
                        offsetXInPlaneFrame = ((cameraCenterY - objectY) * adjustedGSDH) + offsetCenterY

                        # exclude values that are too far away as noise
                        if offsetXInPlaneFrame > maxDistance:
                            continue

                        # exclude values when plane too low
                        if altitude < 5:
                            continue

                        # north is hdg value of 0/360, convert to normal radians with positive
                        # being counter clockwise
                        rotation = (90 - planeHeading) * (math.pi / 180)

                        # Plane is rotated around world frame by heading, so rotate camera detection back
                        worldXinMeters = offsetXInPlaneFrame * math.cos(rotation) - offsetYInPlaneFrame * math.sin(
                            rotation
                        )
                        worldYinMeters = offsetXInPlaneFrame * math.sin(rotation) + offsetYInPlaneFrame * math.cos(
                            rotation
                        )

                        # Simple meters to lat/lon, can be improved. 1 degree is about 111111 meters
                        objectLon = planeLon + (worldXinMeters * (1 / 111111 * math.cos(planeLat * math.pi / 180)))
                        objectLat = planeLat + (worldYinMeters * (1 / 111111.0))

                        # update
                        if 'track_id' in detection:
                            name = detection['name'] + str(detection['track_id'])
                        else:
                            name = detection['name'] + str(i)

                        if detection['name'] == 'person':
                            color = 'red'
                        if detection['name'] == 'car':
                            color = 'green'
                        detectionUpdate = {
                            "id": name,
                            "lat": objectLat,
                            "lon": objectLon,
                            "alt": 0.0,
                            "time": time.time(),
                            "color": color,
                            "type": detection['name'],
                        }
                        self.updatePositions(detectionUpdate)

                # after all detections are done in a frame cycle, compute hulls for groups
                self.computeHulls()

            self.fsInterface.showFrame(frame)
            dataTimeout = 0

        print("closing analyze loop")
