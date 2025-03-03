import cv2

from ultralytics import YOLO
from utils import RunMode
import time
import threading


class frameScanner:

    def __init__(self, video, yoloModel, mode, timestamp):
        self.stopSignal = False

        self.cam = cv2.VideoCapture(video)
        self.width = 1920
        self.height = 1080
        self.fps = self.cam.get(cv2.CAP_PROP_FPS)
        self.frameTime = 1 / self.fps
        self.lastFrame = None
        self.hasFrame = False
        print("FPS is: ", self.fps)

        self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.waitTime = 100000

        self.mode = mode
        print("Size is: ", self.width, " x ", self.height)
        if self.mode is RunMode.LIVE:
            self.waitTime = 1
            size = (self.width, self.height)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.writer = cv2.VideoWriter(f"videos/capture_{timestamp}.mp4", fourcc, self.fps, size)
            self.detectionWriter = cv2.VideoWriter(f"videos/detections_{timestamp}.mp4", fourcc, self.fps, size)
        else:
            self.duration = int(self.cam.get(cv2.CAP_PROP_FRAME_COUNT)) / self.fps

        self.model = YOLO(yoloModel)
        self.framePoll = threading.Thread(target=self.pollFrames)
        self.framePoll.start()

    def shutdown(self):
        self.stopSignal = True
        self.cam.release()
        self.framePoll.join()

        if self.mode is RunMode.LIVE:
            self.writer.release()
            self.detectionWriter.release()

    def pollFrames(self):
        while not self.stopSignal:
            startTime = time.time()
            ret, frame = self.cam.read()
            self.hasFrame = ret
            if ret:
                self.lastFrame = frame
                if self.mode is RunMode.LIVE:
                    self.writer.write(frame)
                else:
                    timeDif = time.time() - startTime
                    if self.frameTime - timeDif > 0:
                        time.sleep(self.frameTime - timeDif)
        print("closing video stream")

    def getFrame(self):
        return self.hasFrame, self.lastFrame

    def getIdentifiedFrame(self, frame):
        results = None
        detectionsFrame = None
        results = self.model.track(frame, persist=True, verbose=False)
        detectionsFrame = results[0].plot()

        if self.mode is RunMode.LIVE:
            self.detectionWriter.write(detectionsFrame)

        return detectionsFrame, results

    def showFrame(self, frame):
        cv2.imshow('PlaneOfView', frame)
        cv2.waitKey(self.waitTime)

    def trainModel(self, data, epochs, patience, batch=-1):
        self.model.train(
            data=data,
            epochs=epochs,
            patience=patience,
            batch=batch,
        )
