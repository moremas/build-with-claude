"""Sea Buddy view: ocean scene, turtle drawing, HUD, wizard, screens."""
import math
import random
import time
import M5
import _sb_core as C

_LCD=M5.Lcd
_W=C._W;_H=C._H;_PLAY_Y=C._PLAY_Y;_PLAY_H=C._PLAY_H;_FLOOR_Y=C._FLOOR_Y
_TX=C._TX;_TY=C._TY;_T_W=C._T_W;_T_H=C._T_H


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass


def _center(text,y,color,bg=C._BLACK,size=1):
    _LCD.setTextSize(size);_LCD.setTextColor(color,bg)
    _LCD.drawString(text,max(0,(_W-_LCD.textWidth(text))//2),y)


def _header(title,right=""):
    _LCD.fillRect(0,0,_W,20,C._DARK);_LCD.fillRect(0,20,_W,1,C._ORANGE)
    _LCD.setTextSize(1);_LCD.setTextColor(C._ORANGE,C._DARK)
    _LCD.drawString(title,6,5)
    if right:
        _LCD.setTextColor(C._CREAM,C._DARK)
        _LCD.drawString(right,_W-6-_LCD.textWidth(right),5)


def _hints(text):
    _LCD.fillRect(0,_H-18,_W,18,C._DARK);_LCD.setTextColor(C._GRAY,C._DARK)
    _LCD.drawString(text,max(0,(_W-_LCD.textWidth(text))//2),_H-14)


def _ellipse(cx,cy,rx,ry,col):
    try:
        _LCD.fillEllipse(cx,cy,rx,ry,col);return
    except Exception:
        pass
    for dy in range(-ry,ry+1):
        w=int(rx*math.sqrt(max(0.0,1.0-(dy/ry)**2)))
        _LCD.fillRect(cx-w,cy+dy,2*w+1,1,col)


_BANDS=(C._SHALLOW,C._MID,0x0E3F6E,C._DEEP)


def _band(y):
    h=_FLOOR_Y-_PLAY_Y;t=max(0,min(3,(y-_PLAY_Y)*4//max(1,h)));return _BANDS[t]


class Ocean:
    def __init__(self):
        self.bub=[];self.fx=-30;self.fy=_PLAY_Y+30;self.fd=1;self.fa=False
        self.kp=0.0;self.kprev=[];self.fprev=None

    def paint_static(self):
        bh=(_FLOOR_Y-_PLAY_Y)//4+1;y=_PLAY_Y
        for c in _BANDS:
            _LCD.fillRect(0,y,_W,bh,c);y+=bh
        for i in range(4):
            x0=30+i*50;_LCD.drawLine(x0,_PLAY_Y,x0+18,_PLAY_Y+34,C._FOAM)
        _LCD.fillRect(0,_FLOOR_Y,_W,_PLAY_Y+_PLAY_H-_FLOOR_Y,C._SAND)
        for i in range(10):
            _LCD.fillRect(8+i*24+(i%3)*4,_FLOOR_Y+4+(i%4)*2,3,2,0x9E7E47)
        _LCD.fillCircle(_W-22,_FLOOR_Y-1,8,C._PINK)
        _LCD.fillCircle(_W-32,_FLOOR_Y-1,6,0xE07A9B)
        _LCD.fillCircle(_W-14,_FLOOR_Y-2,5,0xFFB3C8)

    def tick(self,frame):
        self._kelp();self._bubbles();self._fish(frame)

    def _kelp(self):
        for (x,y) in self.kprev:
            _LCD.fillRect(x-2,y,8,4,_band(y))
        self.kprev=[];self.kp+=0.18
        for st,bx in enumerate((14,24,36)):
            top=_FLOOR_Y-26-st*4;n=(_FLOOR_Y-top)//5
            for i in range(n):
                yy=_FLOOR_Y-i*5
                sway=int(math.sin(self.kp+st*1.4+i*0.55)*(1+i*0.6))
                xx=bx+sway
                col=C._KELP if (i+st)%2==0 else C._KELP2
                _LCD.fillRect(xx-1,yy-4,4,4,col);self.kprev.append((xx-1,yy-4))

    def _bubbles(self):
        keep=[]
        for b in self.bub:
            _LCD.fillCircle(int(b[0]),int(b[1]),b[2],_band(int(b[1])))
            b[1]-=b[3];b[0]+=random.choice((-1,0,0,1))
            if b[1]>_PLAY_Y+4:
                _LCD.drawCircle(int(b[0]),int(b[1]),b[2],C._FOAM);keep.append(b)
        self.bub=keep
        if len(self.bub)<5 and random.random()<0.22:
            self.bub.append([random.randint(20,_W-20),_FLOOR_Y-4,
                             random.choice((1,1,2,2,3)),C._rng(0.9,2.0)])

    def burst(self,cx,cy,n=8):
        for _ in range(n):
            self.bub.append([cx+random.randint(-10,10),cy+random.randint(-4,4),
                             random.choice((1,2,2,3)),C._rng(1.1,2.4)])

    def _fish(self,frame):
        if self.fprev:
            x,y=self.fprev;_LCD.fillRect(x-9,y-5,19,11,_band(y));self.fprev=None
        if not self.fa:
            if random.random()<0.006:
                self.fa=True;self.fd=random.choice((1,-1))
                self.fx=-12 if self.fd>0 else _W+12
                self.fy=random.randint(_PLAY_Y+14,_PLAY_Y+46)
            return
        self.fx+=self.fd*3;bob=int(math.sin(frame*0.4)*2)
        x,y=int(self.fx),self.fy+bob
        col=random.choice((C._YELLOW,C._PINK,C._ORANGE))
        _ellipse(x,y,6,3,col)
        tx=x-self.fd*8
        _LCD.fillTriangle(tx,y,tx-self.fd*5,y-4,tx-self.fd*5,y+4,col)
        _LCD.fillCircle(x+self.fd*3,y-1,1,C._BLACK)
        self.fprev=(x,y)
        if self.fx<-20 or self.fx>_W+20:
            self.fa=False;self.fprev=None


def _face(cx,cy,emo,r):
    ex=max(2,r);gap=ex*3;lx=cx-gap//2;rx=cx+gap//2;ey=cy-1;B=C._BLACK
    if emo=="asleep":
        for x in (lx,rx):_LCD.fillRect(x-ex,ey,ex*2+1,2,B)
        _LCD.fillRect(cx-ex,cy+ex+2,ex*2,1,B);return
    if emo=="dead":
        for x in (lx,rx):
            _LCD.drawLine(x-ex,ey-ex,x+ex,ey+ex,B);_LCD.drawLine(x-ex,ey+ex,x+ex,ey-ex,B)
        _LCD.fillRect(cx-ex,cy+ex+2,ex*2,1,B);return
    if emo=="happy":
        for x in (lx,rx):
            _LCD.drawLine(x-ex,ey+1,x,ey-ex,B);_LCD.drawLine(x,ey-ex,x+ex,ey+1,B)
        _LCD.drawLine(cx-ex-1,cy+ex+1,cx,cy+ex+3,B);_LCD.drawLine(cx,cy+ex+3,cx+ex+1,cy+ex+1,B);return
    if emo=="angry":
        for x,sl in ((lx,1),(rx,-1)):
            _LCD.fillCircle(x,ey,ex,B)
            _LCD.drawLine(x-ex-1,ey-ex-1+(0 if sl>0 else 2),x+ex+1,ey-ex+1-(0 if sl>0 else 2),C._RED)
        _LCD.fillRect(cx-ex,cy+ex+2,ex*2,2,B);return
    if emo=="hungry":
        for x in (lx,rx):
            _LCD.fillCircle(x,ey,ex,B);_LCD.fillCircle(x-1,ey-1,1,C._CREAM)
        _LCD.fillCircle(cx,cy+ex+3,ex,B);return
    if emo in ("sad","depressed"):
        for x in (lx,rx):
            _LCD.fillCircle(x,ey,ex,B);_LCD.fillRect(x-1,ey+ex+1,2,3,C._SHALLOW)
        _LCD.drawLine(cx-ex,cy+ex+4,cx+ex,cy+ex+2,B);return
    if emo=="worried":
        for x in (lx,rx):
            _LCD.drawCircle(x,ey,ex+1,B);_LCD.fillCircle(x,ey,max(1,ex-1),B)
        _LCD.drawLine(cx-ex,cy+ex+3,cx+ex,cy+ex+3,B);return
    for x in (lx,rx):
        _LCD.fillCircle(x,ey,ex,B);_LCD.fillCircle(x-1,ey-1,1,C._CREAM)
    _LCD.fillRect(cx-ex,cy+ex+2,ex*2,1,B)


_SIZES={1:(14,7),2:(20,9),3:(26,12),4:(32,15),5:(38,17)}


def draw_turtle(s,frame):
    x0=_TX-_T_W//2;y0=_TY-_T_H//2
    for yy in range(y0,min(y0+_T_H,_FLOOR_Y),4):
        _LCD.fillRect(x0,yy,_T_W,4,_band(yy))
    if y0+_T_H>_FLOOR_Y:
        _LCD.fillRect(x0,_FLOOR_Y,_T_W,y0+_T_H-_FLOOR_Y,C._SAND)
    emo=C._emotion(s)
    bob=int(math.sin(frame*0.22)*3);flip=int(math.sin(frame*0.45)*4)
    st=s["stage"];sc=C._GOLD if s["gold"] else C._SHELL
    sh=0xFFE9A8 if s["gold"] else C._SHELL2
    if st==0:
        cy=_FLOOR_Y-16+(1 if frame%16<8 else 0)
        _ellipse(_TX,cy,16,20,C._CREAM)
        _LCD.drawLine(_TX-8,cy-4,_TX-2,cy+1,C._GRAY)
        _LCD.drawLine(_TX-2,cy+1,_TX+6,cy-3,C._GRAY)
        _LCD.drawLine(_TX+1,cy+6,_TX+8,cy+2,C._GRAY)
        if frame%30<15:_LCD.drawCircle(_TX,cy,24,C._FOAM)
        return
    br,hr=_SIZES.get(st,(20,9));bcy=_TY+bob
    if s["asleep"]:bcy=_FLOOR_Y-br-2
    flx=_TX-br-2
    _LCD.fillTriangle(flx,bcy-4+flip,flx-br,bcy-10+flip,flx-4,bcy+2+flip,C._SKIN)
    _LCD.fillTriangle(flx,bcy+4-flip,flx-br+4,bcy+12-flip,flx-2,bcy+8-flip,C._SKIN)
    frx=_TX+br+2
    _LCD.fillTriangle(frx,bcy-4-flip,frx+br,bcy-10-flip,frx+4,bcy+2-flip,C._SKIN)
    _LCD.fillTriangle(frx,bcy+4+flip,frx+br-4,bcy+12+flip,frx+2,bcy+8+flip,C._SKIN)
    _LCD.fillCircle(_TX,bcy,br,sc);_LCD.fillCircle(_TX-br//4,bcy-br//4,br//2,sh)
    if st>=3:
        for ang in (0,60,120,180,240,300):
            a=math.radians(ang)
            _LCD.drawCircle(_TX+int(math.cos(a)*br*0.55),bcy+int(math.sin(a)*br*0.55),max(2,br//5),0x3E6E48)
    hcx,hcy=_TX,bcy-br-hr+3
    _LCD.fillCircle(hcx,hcy,hr,C._SKIN);_face(hcx,hcy,emo,max(2,hr//3))
    if s["pearl"]:
        _LCD.fillCircle(hcx+hr-1,hcy-hr+2,3,C._FOAM);_LCD.drawCircle(hcx+hr-1,hcy-hr+2,3,C._GRAY)
    if s["barnacle"] and st>=4:
        _LCD.fillCircle(_TX+br-5,bcy+br-7,4,C._CREAM);_LCD.fillCircle(_TX+br-5,bcy+br-7,2,C._GRAY)
    if st==5:
        for dx in (-4,0,4):
            _LCD.fillTriangle(hcx+dx-2,hcy-hr-1,hcx+dx,hcy-hr-7,hcx+dx+2,hcy-hr-1,C._GOLD)
    if s["asleep"]:
        for i,sz in enumerate((1,2)):
            zx,zy=hcx+14+i*9,hcy-12-i*7
            _LCD.setTextSize(sz);_LCD.setTextColor(C._FOAM,_band(zy));_LCD.drawString("z",zx,zy)
        _LCD.setTextSize(1)


def draw_hud(s):
    _header("Sea Buddy",C._STAGES[s["stage"]])
    bx,by,bw,bh=_W-50,_PLAY_Y+4,40,12
    _LCD.fillRect(bx-2,by-2,bw+8,bh+4,C._DEEP)
    _LCD.drawRect(bx,by,bw,bh,C._CREAM);_LCD.fillRect(bx+bw,by+3,3,bh-6,C._CREAM)
    pct=max(0,min(100,int(s["battery"])));fw=max(0,(bw-4)*pct//100)
    fc=C._GREEN if pct>55 else (C._YELLOW if pct>25 else C._RED)
    _LCD.fillRect(bx+2,by+2,bw-4,bh-4,C._DARK)
    if fw:_LCD.fillRect(bx+2,by+2,fw,bh-4,fc)
    pips=(("HUN",100-s["hunger"],C._YELLOW),("JOY",s["happy"],C._PINK),
          ("CLN",s["clean"],C._FOAM),("NRG",s["energy"],C._SHELL2))
    py=_PLAY_Y+4
    for lab,val,col in pips:
        _LCD.fillRect(4,py,52,11,C._DEEP);_LCD.setTextSize(1)
        _LCD.setTextColor(C._GRAY,C._DEEP);_LCD.drawString(lab,6,py+1)
        bw2=max(0,min(24,int(24*val/100)))
        _LCD.drawRect(30,py+3,26,6,C._GRAY);_LCD.fillRect(31,py+4,24,4,C._DARK)
        if bw2:_LCD.fillRect(31,py+4,bw2,4,col)
        py+=13


_WIZ=(
    ("Welcome, ocean keeper","The Oceanic Preservation","Society found a lost egg.","Will you raise it?"),
    ("Meet your Sea Buddy","It hatches into a sea","turtle and grows through","five life stages over time."),
    ("F = Feed","Krill and plankton lower","hunger. Feed when the HUN","bar runs low. Don't overdo it."),
    ("P = Pet / comfort","Gentle shell pats raise","happiness and calm worry.","Turtles love this."),
    ("C = Clean the ocean","Plastic drifts in over","time. Scoop it out to keep","your buddy's water sparkling."),
    ("L = Play   Z = Nap","Play boosts joy but burns","energy. Tuck it in with Z","when the NRG bar is low."),
    ("Battery = lifespan","The bar up top is its life.","Care fills it; neglect","drains it. Zero = goodbye."),
    ("Evolution","Good care speeds growth.","Egg > Hatchling > Tot >","Juvenile > Adult > ???"),
    ("R = Reset   I = Stats","R (twice) starts a fresh","egg. I shows your record.","W reopens this guide."),
    ("Look closely...","Caring keepers find hidden","things in the deep. Try","typing words to the sea."),
    ("Ready?","Q / ESC exits to launcher.","Press Enter to meet","your egg. Good luck!"),
)


def wizard(kb):
    page=0;C._play("happy")
    while True:
        _LCD.fillScreen(C._DEEP);_header("Sea Buddy - Guide","{}/{}".format(page+1,len(_WIZ)))
        for _ in range(10):
            _LCD.drawCircle(random.randint(8,_W-8),random.randint(_PLAY_Y+6,_H-30),random.choice((1,2)),C._MID)
        ln=_WIZ[page];_center(ln[0],_PLAY_Y+12,C._ORANGE,C._DEEP);y=_PLAY_Y+34
        for l in ln[1:]:_center(l,y,C._CREAM,C._DEEP);y+=16
        _hints("</> page   Enter start   Q quit")
        time.sleep_ms(220)
        while True:
            kb.tick();it=C._intent(kb.get_key(),nav=True)
            if it in ("right","down"):page=min(len(_WIZ)-1,page+1);C._tone(659,30);break
            if it in ("left","up"):page=max(0,page-1);C._tone(523,30);break
            if it=="ok":C._play("boot");return True
            if it=="exit":return False
            time.sleep_ms(40)


def stats_screen(kb,s):
    _LCD.fillScreen(C._DEEP);_header("Buddy stats",C._STAGES[s["stage"]])
    lines=("Alive: {} min".format(int(s["alive_sec"]//60)),
           "Stage: {} ({}/5)".format(C._STAGES[s["stage"]],s["stage"]),
           "Battery: {}%".format(int(s["battery"])),
           "Feeds {}   Pets {}".format(s["feeds"],s["pets"]),
           "Cleans {}  Plays {}".format(s["cleans"],s["plays"]),
           "Secrets: {}{}{}{}".format("G" if s["gold"] else "-","P" if s["pearl"] else "-",
                                      "B" if s["barnacle"] else "-","O" if s["ops_seen"] else "-"))
    y=_PLAY_Y+8
    for ln in lines:_center(ln,y,C._CREAM,C._DEEP);y+=15
    _hints("any key to return");time.sleep_ms(250)
    while True:
        kb.tick()
        if kb.get_key() is not None:return
        time.sleep_ms(40)


def confirm_reset(kb):
    _LCD.fillScreen(C._DEEP);_header("Reset Sea Buddy")
    _center("Release your buddy",_PLAY_Y+18,C._RED,C._DEEP)
    _center("back to the wild?",_PLAY_Y+34,C._RED,C._DEEP)
    _center("This starts a new egg.",_PLAY_Y+56,C._GRAY,C._DEEP)
    _hints("R again to confirm   any key cancel");C._play("worry");time.sleep_ms(300)
    while True:
        kb.tick();k=kb.get_key();it=C._intent(k)
        if it=="reset":return True
        if k is not None:return False
        time.sleep_ms(40)


def game_over(kb,s):
    _LCD.fillScreen(C._BLACK);_header("Sea Buddy")
    _center("Your buddy drifted away.",_PLAY_Y+18,C._GRAY)
    _center("It lived {} min and reached".format(int(s["alive_sec"]//60)),_PLAY_Y+38,C._CREAM)
    _center("the {} stage.".format(C._STAGES[s["stage"]]),_PLAY_Y+54,C._CREAM)
    _center("Real oceans need keepers too.",_PLAY_Y+78,C._ORANGE)
    _hints("R new egg   Q exit");C._play("death")
    while True:
        kb.tick();it=C._intent(kb.get_key())
        if it=="reset":return "reset"
        if it=="exit":return "exit"
        time.sleep_ms(40)


def toast(text):
    _LCD.fillRect(8,_PLAY_Y+2,_W-16,16,C._DARK);_LCD.drawRect(8,_PLAY_Y+2,_W-16,16,C._ORANGE)
    _center(text[:40],_PLAY_Y+6,C._CREAM,C._DARK)


def clear_toast():
    for yy in range(_PLAY_Y+2,_PLAY_Y+18,4):_LCD.fillRect(8,yy,_W-16,4,_band(yy))


def ops_screen(kb,lines):
    _LCD.fillScreen(C._DEEP);_header("* Hidden message *");y=_PLAY_Y+14
    for ln in lines:
        _center(ln,y,C._GOLD if ln==lines[0] else C._CREAM,C._DEEP);y+=16
    for _ in range(20):
        _LCD.drawCircle(random.randint(8,_W-8),random.randint(_PLAY_Y+4,_H-24),random.choice((1,2)),C._GOLD)
    _hints("any key to return");C._play("secret");time.sleep_ms(300)
    while True:
        kb.tick()
        if kb.get_key() is not None:return
        time.sleep_ms(40)


def evolve_screen(s):
    _LCD.fillScreen(C._DEEP);_header("Evolution!")
    _center("Your buddy grew into",_PLAY_Y+18,C._CREAM,C._DEEP)
    _center("a "+C._STAGES[s["stage"]]+"!",_PLAY_Y+40,C._GOLD,C._DEEP)
    if s["stage"]==5:
        _center("An Ancient of the deep.",_PLAY_Y+62,C._ORANGE,C._DEEP)
        _center("OPS salutes you.",_PLAY_Y+78,C._ORANGE,C._DEEP)
    for _ in range(24):
        _LCD.drawCircle(random.randint(8,_W-8),random.randint(_PLAY_Y,_H-22),random.choice((1,2,3)),C._FOAM)
    _hints("");C._play("evolve");time.sleep_ms(1400)


def wave_anim(ocean):
    for sw in range(3):
        for x in range(0,_W+30,14):
            for yy in range(_PLAY_Y,_FLOOR_Y,6):
                wob=int(math.sin((x+sw*40)*0.08+yy*0.1)*3)
                _LCD.fillRect(max(0,x-14),yy,14,6,_band(yy+wob))
            time.sleep_ms(6)
    ocean.paint_static();C._play("secret")
