# DRAFT reply to GitHub issue #2 (ejb1123, custom camera hardware / Seeed reCamera)

**NOT POSTED.** Read it, change what sounds wrong, then post it yourself.

Written to his voice rules: plain, only things that can be checked, and no
em-dashes.

---

Thanks for this, and sorry for the slow reply.

Short answer: there is no custom hardware yet, and this is probably the most
useful open question in the project right now. So yes, very interested.

Here is where it actually stands, so you can judge whether the reCamera fits.

**What a node has to do.** Recognition runs on the device. What leaves it is a
few hundred bytes of structured claim plus one cropped still, and the crop is
capped at 200px on its long edge before it is sent, because that is the size
that destroys a plate. No video ever leaves the camera. That cap is not a
setting I am willing to raise, so any hardware has to be able to do useful work
under it.

**How the work is split today.** The camera or phone runs a small YOLO to find
vehicles and cut the crop. A machine at home then runs CLIP plus a small trained
head to decide whether the vehicle is government. The detection half is cheap.
The classification half is the expensive one, and it is the part I do not know
how to fit on a small board yet.

**So the question I cannot answer is whether the classifier converts.** There is
a Raspberry Pi AI HAT+ (Hailo-8L) on the funding list purely to settle it, and
nobody has confirmed either way. If the reCamera's NPU can carry that half, it
changes the shape of the whole project, because then a node can decide for
itself instead of shipping a crop home.

**Things I would want to know about your build:**

- What is actually running on the device, and at what frame rate and input size?
- Have you had anything larger than a detector on that NPU? CLIP-class models
  are where this falls over for me.
- What resolution does it hold, and at what distance is a vehicle still large
  enough to judge? My working bar is about 120px of vehicle width for the
  classifier to have a chance, and about 60px of plate width before OCR can read
  characters. Those two numbers decide where a camera can usefully be placed.
- Night. Everything I have measured goes quiet after dark rather than because
  traffic stops.

I will read through Authority Alert properly. If there is a sensible way to have
the reCamera speak the same enrolment and upload protocol the phone nodes use,
that is probably the smallest first step, and I would rather build toward
something you have already got working than start a parallel thing.

One thing to flag early, because it would matter to me and might to you: this
project keeps two tiers on purpose. Government vehicles are public and
searchable, everybody else is hashed at the camera and deleted on a short clock.
Any hardware path has to keep that split enforceable on the device rather than
in a policy document. Happy to go through how that works if it is useful.

---

## Notes for you, not for the reply

- I did not state anything about the reCamera's specs, because I have not
  verified them. Everything about their hardware is asked rather than asserted.
- The four questions are the ones whose answers actually decide this. If they
  come back with a frame rate and a model, you will know within a message
  whether it is worth building toward.
- If you would rather not commit to reading their repo, cut that paragraph. It
  is the only promise in here.
- Worth checking their licence before adopting anything: SparrowMap is AGPL-3.0
  and that constrains what can be folded in.
