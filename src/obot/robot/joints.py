"""Servo/joint IDs shared by the hardware controller and the digital simulator.

Values match the ohbot library's motor constants, so a joint id can be passed
straight to ``ohbot.move``. Positions are 0..10 with 5 = neutral/rest.
"""

from __future__ import annotations

HEADNOD = 0
HEADTURN = 1
EYETURN = 2
LIDBLINK = 3
TOPLIP = 4
BOTTOMLIP = 5
EYETILT = 6

#* Joints the motor mixer manages. HEADROLL (7) exists on Picoh only.
ALL_JOINTS = (HEADNOD, HEADTURN, EYETURN, LIDBLINK, TOPLIP, BOTTOMLIP, EYETILT)

#* Joints that must snap rather than glide: lips during speech and eyelids for blinks.
FAST_JOINTS = frozenset({LIDBLINK, TOPLIP, BOTTOMLIP})

JOINT_NAMES = {
    HEADNOD: "HeadNod",
    HEADTURN: "HeadTurn",
    EYETURN: "EyeTurn",
    LIDBLINK: "LidBlink",
    TOPLIP: "TopLip",
    BOTTOMLIP: "BottomLip",
    EYETILT: "EyeTilt",
}

REST_POSITION = 5.0
