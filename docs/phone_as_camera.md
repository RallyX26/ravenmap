# "How do I use my phone as a camera on my computer?"

Answer to paste. The short version is that they probably do not need the
computer, and the long version has two routes if they want it anyway.

---

## Start here: you may not need a computer at all

Open **map.sparrowmap.com/app** on the phone. That IS a camera node. It runs the
vehicle detector in the browser, crops on the device, and posts a 200px crop.
Nothing is installed, no desktop is involved, and no video ever leaves the phone.

That is the intended path for most people, so ask what the computer is for before
recommending anything else. Good reasons to want it:

* the desktop can run the bigger classifier locally, which the phone browser cannot;
* an old phone that cannot run the browser detector;
* they want the phone mounted permanently and would rather the computer did the work.

---

## Route A: an IP camera app. Simplest, no drivers.

SparrowMap's detector already takes a URL, because its default source is
camctl's MJPEG stream. So a phone that serves MJPEG or RTSP plugs straight in.

1. Install an IP camera app. On Android **IP Webcam** is the usual one; on iOS
   any app that serves MJPEG or RTSP works.
2. Start the server in the app. It shows a URL like `http://192.168.1.44:8080/video`.
3. On the computer, point the node at it:

```
python detect\run_live.py --source http://192.168.1.44:8080/video ^
    --post --visual --node n_xxxxxxx --token YOUR_TOKEN
```

Get the node id and token from **/app** once, or from `/key`.

**Why this route first:** no driver, no vendor software on the desktop, and it is
the same shape as every other camera the project already reads.

## Route B: a webcam driver. Use it if they already have one.

Iriun, DroidCam and EpocCam make the phone appear as an ordinary webcam.

1. Install the app on the phone and its driver on the computer.
2. Start **camctl**, which opens the device and re-serves it:

```
python camctl\camctl.py        ->  http://localhost:8160/
```

3. Then run the node with no `--source` at all. It consumes camctl's stream by
   default.

**Why camctl and not `--source 0`:** on Windows exactly one process can hold a
webcam. If the node opens it, camctl cannot, and aiming becomes impossible.
camctl owns the device and re-serves it over HTTP so any number of consumers can
read it. `--source 0` does now work if they insist, but they lose aiming.

---

## Things worth telling them up front

* **Plug it in.** A phone streaming video continuously will not last an evening
  on battery, and it will get warm.
* **Same network**, or Tailscale. The URL in Route A is a LAN address.
* **Aim matters more than the phone.** The bar is about 120px of vehicle width
  before the classifier has anything to work with, so a phone pointed at a wide
  street from a long way back will produce crops nothing can rule on. `/hardware`
  has the measured numbers.
* **Behind glass, turn autofocus off.** It hunts between the pane and the street
  and every re-hunt is a second of blur.
* **Nothing changes about privacy on either route.** Recognition still happens
  before anything is uploaded, the crop is still capped at 200px, and no video
  leaves the device.
