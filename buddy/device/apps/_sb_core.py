"""Sea Buddy core: palette, sounds, state, decay, evolution, easter eggs."""
import json
import os
import random
import time
import M5

_BLACK=0x000000;_ORANGE=0xCC785C;_CREAM=0xF0EEE6;_DARK=0x1F1F1F;_GRAY=0x777777
_DEEP=0x06283D;_MID=0x1363DF;_SHALLOW=0x47B5FF;_FOAM=0xDFF6FF;_SAND=0xC8A165
_KELP=0x2E6E4E;_KELP2=0x3D8B66;_SHELL=0x4F8A5B;_SHELL2=0x6FBF73;_SKIN=0x8AC9A0
_GOLD=0xFFD56B;_RED=0xE85050;_PINK=0xFF8FB1;_GREEN=0x4CAF50;_YELLOW=0xFFC857
_LCD=M5.Lcd;_W=240;_H=135
_PLAY_Y=21;_PLAY_H=_H-18-_PLAY_Y;_FLOOR_Y=_PLAY_Y+_PLAY_H-14
_TX=130;_TY=_FLOOR_Y-22;_T_W=110;_T_H=76
_SAVE_PATH="/flash/sea_buddy.json"
_EVOLVE_SEC=3600;_GROWTH_PER_CARE=300;_TICK_MS=90;_SAVE_EVERY_SEC=30
_STAGES=("Egg","Hatchling","Tot","Juvenile","Adult","Ancient")


def _rng(lo,hi):
    return lo+random.random()*(hi-lo)


def _tone(f,ms):
    try:
        M5.Speaker.tone(int(f),int(ms))
    except Exception:
        pass


def _melody(notes):
    for f,d in notes:
        if f:
            _tone(f,d)
        time.sleep_ms(int(d)+12)


_SND={
    "boot":[(523,70),(659,70),(784,90),(1046,130)],
    "happy":[(659,50),(784,50),(988,90)],
    "comfort":[(440,90),(494,90),(523,140)],
    "eat":[(330,35),(262,35),(330,35),(262,50)],
    "yum":[(523,60),(659,60),(880,110)],
    "hungry":[(392,110),(330,130)],
    "angry":[(196,60),(175,60),(196,60),(147,110)],
    "worry":[(440,70),(415,70),(440,70),(415,90)],
    "sad":[(392,130),(330,130),(262,200)],
    "sleep":[(330,130),(262,160),(220,220)],
    "wake":[(262,70),(392,70),(523,100)],
    "clean":[(880,40),(988,40),(1175,60)],
    "play":[(523,50),(659,50),(523,50),(784,70)],
    "evolve":[(392,90),(523,90),(659,90),(784,110),(1046,220)],
    "secret":[(659,60),(880,60),(1046,60),(1318,60),(1568,110)],
    "gold":[(1046,70),(1175,70),(1318,70),(1568,70),(2093,160)],
    "death":[(392,200),(330,200),(262,220),(196,350)],
    "deny":[(196,90),(165,130)],
}


def _play(n):
    s=_SND.get(n)
    if s:
        _melody(s)


_DEFAULT={
    "name":"Bubbles","hunger":30,"happy":70,"clean":90,"energy":90,
    "battery":100,"alive_sec":0,"growth_sec":0,"stage":0,"asleep":False,
    "wizard_done":False,"feeds":0,"pets":0,"cleans":0,"plays":0,
    "best_streak":0,"gold":False,"pearl":False,"barnacle":False,
    "ops_seen":False,"dead":False,
}


def _load():
    try:
        with open(_SAVE_PATH,"r") as f:
            d=json.load(f)
        out=dict(_DEFAULT);out.update(d);return out
    except Exception:
        return dict(_DEFAULT)


def _save(s):
    try:
        with open(_SAVE_PATH,"w") as f:
            json.dump(s,f)
    except Exception as e:
        print("sea_buddy save:",e)


def _wipe():
    try:
        os.remove(_SAVE_PATH)
    except Exception:
        pass


def _clamp(v,lo=0,hi=100):
    return max(lo,min(hi,v))


def _emotion(s):
    if s["dead"]:return "dead"
    if s["asleep"]:return "asleep"
    if s["battery"]<18:return "worried"
    if s["hunger"]>82:return "angry"
    if s["hunger"]>62:return "hungry"
    if s["clean"]<25:return "sad"
    if s["happy"]<25:return "depressed"
    if s["happy"]>84:return "happy"
    return "neutral"


def _grow(s,sec):
    s["growth_sec"]+=sec


def _feed(s,burst):
    if s["dead"]:_play("deny");return None
    if s["asleep"]:_play("deny");return "zzz... let it rest"
    bonus=s["hunger"]>60
    s["hunger"]=_clamp(s["hunger"]-38)
    s["happy"]=_clamp(s["happy"]+(12 if bonus else 4))
    s["battery"]=_clamp(s["battery"]+(8 if bonus else 3))
    s["feeds"]+=1;_grow(s,_GROWTH_PER_CARE)
    burst(_TX,_TY-10,4)
    _play("yum" if bonus else "eat")
    return "Nom nom! Krill delivery." if bonus else "Snacking on plankton."


def _pet(s,burst):
    if s["dead"]:_play("deny");return None
    s["happy"]=_clamp(s["happy"]+16);s["battery"]=_clamp(s["battery"]+2)
    s["pets"]+=1;_grow(s,_GROWTH_PER_CARE//2)
    burst(_TX,_TY-30,3);_play("comfort")
    return "Shell pats. Very soothing."


def _clean(s,burst):
    if s["dead"]:_play("deny");return None
    delta=100-s["clean"];s["clean"]=100
    s["battery"]=_clamp(s["battery"]+(6 if delta>50 else 2))
    s["happy"]=_clamp(s["happy"]+6)
    s["cleans"]+=1;_grow(s,_GROWTH_PER_CARE)
    burst(_TX,_TY,10);_play("clean")
    return "Plastic scooped. Ocean sparkles."


def _play_with(s,burst):
    if s["dead"]:_play("deny");return None
    if s["asleep"]:_play("deny");return "zzz..."
    if s["energy"]<12:_play("deny");return "Too tired to play right now."
    s["happy"]=_clamp(s["happy"]+22);s["energy"]=_clamp(s["energy"]-14)
    s["hunger"]=_clamp(s["hunger"]+8)
    s["plays"]+=1;_grow(s,_GROWTH_PER_CARE)
    burst(_TX,_TY-16,6);_play("play")
    return "Chasing bubbles! Wheee!"


def _toggle_sleep(s):
    if s["dead"]:_play("deny");return None
    s["asleep"]=not s["asleep"]
    if s["asleep"]:_play("sleep");return "Tucked in for a nap."
    _play("wake");return "Rise and shine, little fin."


def _decay(s,sec):
    if s["dead"]:return
    s["alive_sec"]+=sec;s["growth_sec"]+=sec
    if s["asleep"]:
        s["energy"]=_clamp(s["energy"]+sec*0.50)
        s["hunger"]=_clamp(s["hunger"]+sec*0.04)
        s["happy"]=_clamp(s["happy"]-sec*0.02)
    else:
        s["hunger"]=_clamp(s["hunger"]+sec*0.10)
        s["happy"]=_clamp(s["happy"]-sec*0.09)
        s["clean"]=_clamp(s["clean"]-sec*0.05)
        s["energy"]=_clamp(s["energy"]-sec*0.04)
    avg=(100-s["hunger"]+s["happy"]+s["clean"]+s["energy"])/4.0
    drift=(avg-s["battery"])*0.012*sec
    if s["hunger"]>92 or s["happy"]<6 or s["clean"]<6 or s["energy"]<3:
        drift-=0.15*sec
    s["battery"]=_clamp(s["battery"]+drift)
    if s["battery"]<=0.5:s["dead"]=True


def _maybe_evolve(s):
    if s["dead"] or s["stage"]>=5:return False
    nxt=s["stage"]+1
    if s["growth_sec"]<nxt*_EVOLVE_SEC:return False
    if nxt==5 and s["battery"]<75:return False
    s["stage"]=nxt;return True


_KONAMI=[";",";",".",".",",","/",",","/","b","a"]


class Eggs:
    def __init__(self):
        self.buf=[]
    def feed(self,ch,s):
        if not ch:return None
        self.buf.append(ch);self.buf=self.buf[-12:]
        b="".join(self.buf)
        if b.endswith("ops") and not s["ops_seen"]:
            s["ops_seen"]=True
            return ("OPS","Oceanic Preservation Society","Every small act of care",
                    "ripples out to the whole sea.","Thank you for being a keeper.")
        if b.endswith("wave"):return ("WAVE",None)
        if b.endswith("kelp"):return ("KELP",None)
        if self.buf[-len(_KONAMI):]==_KONAMI and not s["gold"]:
            s["gold"]=True;return ("GOLD",None)
        return None


def _check_milestones(s):
    if not s["pearl"] and s["pets"]>=15 and s["happy"]>90:
        s["pearl"]=True
        return "* A pearl washed in. Your buddy keeps it. *"
    if not s["barnacle"] and s["cleans"]>=12 and s["stage"]>=4:
        s["barnacle"]=True
        return "* A tiny barnacle hitches a ride. Friend! *"
    return None


def _keychar(k):
    if k is None:return None
    if isinstance(k,int):
        if k==0x1B:return "\x1b"
        if k in (0x0A,0x0D):return "\n"
        if 0x20<=k<=0x7E:return chr(k).lower()
        return None
    if isinstance(k,str) and k:return k[0].lower()
    return None


_INTENT_MAP={"\x1b":"exit","q":"exit","\n":"ok","f":"feed","p":"pet","c":"clean",
             "l":"play","z":"sleep","w":"wizard","i":"stats","r":"reset"}
_NAV_MAP={";":"up",".":"down",",":"left","/":"right",
          "w":"up","s":"down","a":"left","d":"right"}


def _intent(k,nav=False):
    ch=_keychar(k)
    if ch is None:return None
    if nav:
        if ch in _NAV_MAP:return _NAV_MAP[ch]
        if ch=="\n":return "ok"
        if ch in ("\x1b","q"):return "exit"
        return None
    return _INTENT_MAP.get(ch)


_LAST_EMO=[0]


def _ambient_sound(s):
    if s["dead"] or s["asleep"]:return
    now=time.ticks_ms()
    if time.ticks_diff(now,_LAST_EMO[0])<14000:return
    emo=_emotion(s)
    snd={"hungry":"hungry","angry":"angry","worried":"worry",
         "sad":"sad","depressed":"sad","happy":"happy"}.get(emo)
    if snd:
        _play(snd);_LAST_EMO[0]=now
