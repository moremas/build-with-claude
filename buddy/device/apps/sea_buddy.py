"""Sea Buddy entry — oceanic tamagotchi for the Oceanic Preservation Society."""
import gc
gc.collect()
import time
import M5
import machine
from hardware import MatrixKeyboard
import _sb_core as C
gc.collect()
import _sb_view as V
gc.collect()

_LCD=M5.Lcd


def _fresh(s,ocean):
    _LCD.fillScreen(C._BLACK)
    ocean.paint_static()
    V.draw_hud(s)
    V._hints("F feed P pet C clean L play Z nap I info W ?")


def run():
    V._set_font()
    try:
        M5.Speaker.setVolume(96)
    except Exception:
        pass
    kb=MatrixKeyboard()
    time.sleep_ms(400)
    s=C._load();ocean=V.Ocean();eggs=C.Eggs()
    if not s["wizard_done"]:
        if not V.wizard(kb):
            try:_LCD.fillScreen(C._BLACK)
            except Exception:pass
            time.sleep_ms(150);machine.reset()
        s["wizard_done"]=True;C._save(s)
    _fresh(s,ocean)
    if not s["dead"]:C._play("boot")
    frame=0;last=time.ticks_ms();dacc=0.0;sacc=0.0
    tuntil=0;tactive=False
    try:
        while True:
            now=time.ticks_ms();dt=time.ticks_diff(now,last)/1000.0;last=now
            dacc+=dt;sacc+=dt
            kb.tick();rk=kb.get_key();ch=C._keychar(rk);it=C._intent(rk);msg=None
            if ch and ch not in ("\x1b","\n"):
                hit=eggs.feed(ch,s)
                if hit:
                    k=hit[0]
                    if k=="OPS":V.ops_screen(kb,hit[1:]);_fresh(s,ocean)
                    elif k=="WAVE":V.wave_anim(ocean);_fresh(s,ocean);msg="* The tide turns. *"
                    elif k=="KELP":
                        for _ in range(30):ocean.burst(20+(_*7)%200,C._FLOOR_Y-8,2)
                        C._play("secret");msg="* Kelp forest party! *"
                    elif k=="GOLD":C._play("gold");msg="* Golden shell unlocked! *"
            if it=="feed":msg=C._feed(s,ocean.burst)
            elif it=="pet":msg=C._pet(s,ocean.burst)
            elif it=="clean":msg=C._clean(s,ocean.burst)
            elif it=="play":msg=C._play_with(s,ocean.burst)
            elif it=="sleep":msg=C._toggle_sleep(s)
            elif it=="wizard":V.wizard(kb);_fresh(s,ocean)
            elif it=="stats":V.stats_screen(kb,s);_fresh(s,ocean)
            elif it=="reset":
                if V.confirm_reset(kb):
                    C._wipe();s=C._load();s["wizard_done"]=True;eggs=C.Eggs()
                    C._LAST_EMO[0]=0;_fresh(s,ocean);C._play("boot")
                else:_fresh(s,ocean)
            elif it=="exit":C._save(s);return
            if dacc>=1.0:
                step=dacc;dacc=0.0;C._decay(s,step)
                m=C._check_milestones(s)
                if m:msg=m;C._play("secret")
                if C._maybe_evolve(s):
                    C._save(s);V.evolve_screen(s);_fresh(s,ocean)
            if s["dead"]:
                C._save(s)
                out=V.game_over(kb,s)
                if out=="reset":
                    C._wipe();s=C._load();s["wizard_done"]=True;eggs=C.Eggs()
                    C._LAST_EMO[0]=0;_fresh(s,ocean);C._play("boot");continue
                return
            if msg:
                V.toast(msg);tuntil=time.ticks_ms()+2400;tactive=True
            elif tactive and time.ticks_diff(time.ticks_ms(),tuntil)>0:
                V.clear_toast();tactive=False;V.draw_hud(s)
            ocean.tick(frame);V.draw_turtle(s,frame)
            if frame%10==0 and not tactive:V.draw_hud(s)
            C._ambient_sound(s)
            if sacc>=C._SAVE_EVERY_SEC:sacc=0.0;C._save(s)
            frame+=1;time.sleep_ms(C._TICK_MS)
    finally:
        try:C._save(s)
        except Exception:pass
        try:_LCD.fillScreen(C._BLACK)
        except Exception:pass
        time.sleep_ms(200);machine.reset()


run()
