import os
import json
import time
import urllib.request

IS_LONG = os.environ.get("VIDEO_MODE", "").strip().upper() == "LONG"
OUT_W = 1920 if IS_LONG else 1080
OUT_H = 1080 if IS_LONG else 1920

REMO_FILES = {
    "package.json": r"""{"name":"overlay","version":"1.0.0","private":true,"dependencies":{"remotion":"4.0.352","@remotion/cli":"4.0.352","react":"18.3.1","react-dom":"18.3.1","d3-geo":"3.1.1","@remotion/google-fonts":"4.0.352"}}""",
    "tsconfig.json": r"""{"compilerOptions":{"target":"ES2020","module":"ESNext","moduleResolution":"Bundler","jsx":"react-jsx","strict":false,"skipLibCheck":true,"esModuleInterop":true,"noEmit":true},"include":["src"]}""",
    "src/index.ts": r"""import {registerRoot} from "remotion";
import {Root} from "./Root";
registerRoot(Root);
""",
    "src/Root.tsx": f"""import React from "react";
import {{Composition}} from "remotion";
import {{Overlay}} from "./Overlay";
export const Root: React.FC = () => {{
  return (
    <Composition
      id="Overlay"
      component={{Overlay as any}}
      width={{{OUT_W}}}
      height={{{OUT_H}}}
      fps={{30}}
      durationInFrames={{300}}
      defaultProps={{{{overlays: [], fps: 30, images: [], durationInFrames: 300}}}}
      calculateMetadata={{({{props}}: any) => ({{
        durationInFrames: props.durationInFrames,
        fps: props.fps,
      }})}}
    />
  );
}};
""",
    "src/Overlay.tsx": r"""import React, {createContext, useContext, useEffect, useMemo, useState} from "react";
import {
  AbsoluteFill, Sequence, OffthreadVideo, useCurrentFrame, useVideoConfig, spring, interpolate,
  Easing, random, staticFile, delayRender, continueRender,
} from "remotion";
import {geoMercator, geoPath, geoContains, geoGraticule} from "d3-geo";
import {loadFont as loadMontserrat} from "@remotion/google-fonts/Montserrat";
import {loadFont as loadOswald} from "@remotion/google-fonts/Oswald";
import {loadFont as loadPlayfair} from "@remotion/google-fonts/PlayfairDisplay";

const mont: any = loadMontserrat("normal", {weights: ["800"], subsets: ["latin"]});
const osw: any = loadOswald("normal", {weights: ["700"], subsets: ["latin"]});
const play: any = loadPlayfair("italic", {weights: ["700"], subsets: ["latin"]});

const F_DISPLAY = `${mont.fontFamily}, 'DejaVu Sans', Verdana, sans-serif`;
const F_COND = `${osw.fontFamily}, 'DejaVu Sans Condensed', 'DejaVu Sans', sans-serif`;
const F_SERIF = `${play.fontFamily}, 'DejaVu Serif', Georgia, serif`;

const DEFAULT_C = {
  green: "#1F3D2B", dark: "#162E20", cream: "#F2E8D5",
  orange: "#E08A2E", black: "#111111", mid: "#2D5C43",
};
const ThemeCtx = createContext<any>(DEFAULT_C);
const useC = () => useContext(ThemeCtx);
const CfgCtx = createContext<any>({glass: null});

const clamp: any = {extrapolateLeft: "clamp", extrapolateRight: "clamp"};
const SPR = {damping: 18, stiffness: 220, mass: 0.7};
const up = (s: any) => String(s || "").toUpperCase();
const fitSize = (txt: string, max: number, width: number) =>
  Math.max(24, Math.min(max, width / (Math.max(txt.length, 1) * 0.78)));

const titleCase = (s: any) =>
  String(s || "").toLowerCase().replace(/(^|[\s\/\-])([a-z\u00e0-\u00ff])/g, (_m: string, a: string, b: string) => a + b.toUpperCase());

const keyWord = (text: string): string => {
  const ws = String(text || "").split(/\s+/).filter(Boolean);
  let best = "";
  for (const w of ws) if (w.replace(/[^A-Za-z0-9\u00c0-\u00ff]/g, "").length >= best.replace(/[^A-Za-z0-9\u00c0-\u00ff]/g, "").length) best = w;
  return best;
};

const wrapLines = (rawLines: string[], maxChars = 18): string[] => {
  const out: string[] = [];
  for (const raw0 of rawLines) {
    const s0 = String(raw0 || "");
    const star = s0.trim().startsWith("*");
    const words = s0.replace(/^\s*\*/, "").trim().split(/\s+/).filter(Boolean);
    let cur = "";
    const flush = () => { if (cur) out.push((star ? "*" : "") + cur); cur = ""; };
    for (const w of words) {
      if (!cur) cur = w;
      else if ((cur + " " + w).length <= maxChars) cur += " " + w;
      else { flush(); cur = w; }
    }
    flush();
  }
  return out.slice(0, 4);
};

const Scene: React.FC<any> = ({dur, children, moving = true}) => {
  const f = useCurrentFrame();
  const out = interpolate(f, [dur - 7, dur], [1, 0], clamp);
  const lift = moving ? interpolate(f, [dur - 7, dur], [0, -24], clamp) : 0;
  return (
    <AbsoluteFill style={{opacity: out, transform: `translateY(${lift}px)`, fontFamily: F_DISPLAY}}>
      {children}
    </AbsoluteFill>
  );
};

const Reveal: React.FC<any> = ({delay = 0, children, style = {}, pad = "0.16em"}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = Math.min(1, Math.max(0, spring({frame: f - delay, fps, config: SPR})));
  return (
    <div style={{overflow: "hidden", padding: `${pad} 0`, margin: `-${pad} 0`, ...style}}>
      <div style={{transform: `translateY(${(1 - p) * 110}%)`}}>{children}</div>
    </div>
  );
};

const Mark: React.FC<any> = ({delay = 0, color, alpha = 0.96, children}) => {
  const f = useCurrentFrame();
  const w = interpolate(f, [delay, delay + 10], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <span style={{position: "relative", display: "inline-block", padding: "0 0.26em"}}>
      <span style={{
        position: "absolute", left: 0, right: 0, top: "10%", bottom: "2%",
        background: color, opacity: alpha, borderRadius: 6,
        transformOrigin: "0 50%", transform: `scaleX(${w}) skewX(-6deg)`,
      }} />
      <span style={{position: "relative"}}>{children}</span>
    </span>
  );
};

const MarkedText: React.FC<any> = ({text, delay = 6, color, ink}) => {
  const key = keyWord(text);
  const idx = key ? text.lastIndexOf(key) : -1;
  if (idx < 0 || text.trim().split(/\s+/).length < 2) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <Mark delay={delay} color={color}><span style={{color: ink, WebkitTextStroke: "0px transparent"} as any}>{key}</span></Mark>
      {text.slice(idx + key.length)}
    </>
  );
};

const Dim: React.FC<any> = ({dur}) => {
  const f = useCurrentFrame();
  const a = Math.min(interpolate(f, [0, 8], [0, 1], clamp), interpolate(f, [dur - 8, dur], [1, 0], clamp));
  return (
    <AbsoluteFill style={{
      opacity: a,
      background: "radial-gradient(ellipse at 50% 50%, rgba(0,0,0,0.38) 0%, rgba(0,0,0,0.38) 55%, rgba(0,0,0,0.5) 100%)",
    }} />
  );
};

const Glass: React.FC<any> = ({o, x, y, w, h, radius = 26, style = {}, children}) => {
  const {glass} = useContext(CfgCtx);
  const {fps, width, height} = useVideoConfig();
  const from = Math.round(((o && o.t0) || 0) * fps);
  return (
    <div style={{
      position: "absolute", left: x, top: y, width: w, height: h, borderRadius: radius,
      overflow: "hidden", boxSizing: "border-box",
      background: "rgba(10,14,12,0.44)",
      backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)",
      border: "1px solid rgba(255,255,255,0.22)",
      boxShadow: "0 2px 6px rgba(0,0,0,0.35), 0 18px 40px rgba(0,0,0,0.42), 0 44px 96px rgba(0,0,0,0.32)",
      ...style,
    } as any}>
      {glass ? (
        <div style={{position: "absolute", left: -x, top: -y, width, height}}>
          <OffthreadVideo src={staticFile(glass)} startFrom={from} style={{width, height, objectFit: "cover"}} />
        </div>
      ) : null}
      <div style={{position: "absolute", left: 0, top: 0, right: 0, bottom: 0, background: "linear-gradient(145deg, rgba(var(--d),.50) 0%, rgba(8,12,10,.60) 100%)"}} />
      <div style={{position: "absolute", left: 0, right: 0, top: 0, height: 1, background: "linear-gradient(90deg, transparent, rgba(255,255,255,.55), transparent)"}} />
      {children}
    </div>
  );
};

const Big: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const lines: string[] = wrapLines(o.lines || [], isLand ? 28 : 16);
  const starIdx = lines.findIndex((l) => l.startsWith("*"));
  const accentIdx = starIdx >= 0 ? starIdx : (lines.length > 1 ? lines.length - 1 : -1);
  const sBar = Math.min(1, spring({frame: f - lines.length * 4 - 4, fps, config: SPR}));
  const bgOp = interpolate(f, [0, 8], [0, 1], clamp);
  const boxTop = isLand ? 160 : 330;
  const boxH = isLand ? 560 : 700;
  const maxW = isLand ? 1560 : 860;
  return (
    <Scene dur={dur}>
      <div style={{
        position: "absolute", left: 40, right: 40, top: boxTop, height: boxH, opacity: bgOp,
        background: "radial-gradient(ellipse at center, rgba(8,12,10,0.66) 0%, rgba(8,12,10,0) 72%)",
      }} />
      <div style={{
        position: "absolute", left: 60, right: 60, top: boxTop, height: boxH,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      }}>
        {lines.map((raw, i) => {
          const l = raw.replace(/^\*/, "");
          const accent = i === accentIdx;
          const size = accent
            ? Math.min(isLand ? 92 : 104, Math.max(44, maxW / (Math.max(l.length, 4) * 0.6)))
            : Math.min(isLand ? 98 : 112, Math.max(44, maxW / (Math.max(l.length, 4) * 0.8)));
          const single = lines.length === 1;
          return (
            <Reveal key={i} delay={i * 4} style={{textAlign: "center", maxWidth: width - 140}}>
              <div style={{
                fontFamily: accent ? F_SERIF : F_DISPLAY, fontStyle: accent ? "italic" : "normal",
                fontWeight: accent ? 700 : 800, fontSize: size, lineHeight: 1.12,
                color: accent ? C.black : C.cream, letterSpacing: accent ? 0 : 1.5,
                WebkitTextStroke: accent ? "0px transparent" : `8px ${C.black}`, paintOrder: "stroke fill",
                textShadow: accent ? "none" : "0 8px 34px rgba(0,0,0,0.85)",
                textAlign: "center", padding: "2px 0",
              } as any}>
                {accent ? (
                  <Mark delay={i * 4 + 6} color={C.orange}>{l}</Mark>
                ) : single ? (
                  <MarkedText text={l} delay={8} color={C.orange} ink={C.black} />
                ) : l}
              </div>
            </Reveal>
          );
        })}
        <div style={{
          marginTop: 26, height: 6, borderRadius: 3,
          background: `linear-gradient(90deg, transparent, ${C.orange}, ${C.cream}, transparent)`,
          width: (isLand ? 760 : 560) * sBar,
        }} />
      </div>
    </Scene>
  );
};

const Lower: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const s = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR})));
  const prog = interpolate(f, [0, dur], [0, 100], clamp);
  const badge = up(o.badge || "SOROTAN FAKTA");
  const lines = wrapLines([up(o.text)], isLand ? 42 : 22).slice(0, 2).map((l) => l.replace(/^\*/, ""));
  const size = fitSize(lines.reduce((a, b) => (a.length >= b.length ? a : b), ""), isLand ? 38 : 44, isLand ? 1100 : 820);
  const gx = isLand ? 80 : 60;
  const gy = isLand ? 680 : 880;
  const gw = isLand ? 1240 : 960;
  return (
    <Scene dur={dur} moving={false}>
      <Glass o={o} x={gx} y={gy} w={gw} style={{transform: `translateX(${(1 - s) * -80}px)`, opacity: Math.min(1, s * 1.6)}}>
        <div style={{position: "relative", padding: "22px 34px 26px 46px"}}>
          <div style={{
            position: "absolute", left: 22, top: 22, bottom: 26, width: 6, borderRadius: 3,
            background: `linear-gradient(180deg, ${C.orange}, rgba(var(--a),.25))`,
            boxShadow: `0 0 16px rgba(var(--a),.6)`,
          }} />
          <Reveal delay={2}>
            <div style={{fontFamily: F_COND, fontWeight: 700, fontSize: 23, letterSpacing: 4, color: C.orange}}>{badge}</div>
          </Reveal>
          <div style={{height: 8}} />
          {lines.map((l, i) => (
            <Reveal key={i} delay={6 + i * 4}>
              <div style={{fontWeight: 800, fontSize: size, color: C.cream, lineHeight: 1.22}}>
                <MarkedText text={l} delay={14 + i * 4} color={C.orange} ink={C.black} />
              </div>
            </Reveal>
          ))}
        </div>
        <div style={{position: "absolute", left: 0, bottom: 0, height: 4, width: `${prog}%`, background: `linear-gradient(90deg, ${C.orange}, ${C.cream})`}} />
      </Glass>
    </Scene>
  );
};

const List: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const items: any[] = o.items || [];
  const n = Math.max(items.length, 1);
  const h = Math.min(isLand ? 480 : 520, 115 + 96 * n);
  const top = isLand ? Math.round((height - h) / 2) - 40 : 1040 - h;
  const gx = isLand ? 310 : 60;
  const gw = isLand ? 1300 : 960;
  const p = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR})));
  return (
    <>
      <Dim dur={dur} />
      <Scene dur={dur} moving={false}>
        <Glass o={o} x={gx} y={top} w={gw} h={h} style={{transform: `translateY(${(1 - p) * 60}px)`, opacity: Math.min(1, p * 1.5)}}>
          <div style={{position: "absolute", left: 30, top: 24, bottom: 24, right: 34}}>
            <div style={{
              position: "absolute", left: 0, top: 2, height: 34, width: 6, borderRadius: 3,
              background: `linear-gradient(180deg, ${C.orange}, rgba(var(--a),.3))`,
              boxShadow: "0 0 14px rgba(var(--a),.6)",
            }} />
            <div style={{paddingLeft: 22, paddingBottom: 12, borderBottom: "1px solid rgba(255,255,255,0.14)"}}>
              <Reveal delay={2}>
                <div style={{fontFamily: F_COND, fontWeight: 700, fontSize: 26, letterSpacing: 4, color: C.orange, lineHeight: 1.2}}>
                  {up(o.title || "RINGKASAN FAKTA")}
                </div>
              </Reveal>
            </div>
            <div style={{display: "flex", flexDirection: "column", justifyContent: "space-evenly", position: "absolute", left: 0, right: 0, top: 58, bottom: 0}}>
              {items.map((it, i) => {
                const st = Math.round((it._t || i * 0.45) * fps);
                const q = Math.min(1, Math.max(0, spring({frame: f - st, fps, config: SPR})));
                const txt = up(it.text);
                return (
                  <div key={i} style={{display: "flex", alignItems: "center", gap: 20}}>
                    <div style={{
                      minWidth: 42, height: 42, borderRadius: 12, background: C.orange, color: C.black,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontFamily: F_DISPLAY, fontWeight: 800, fontSize: 22,
                      transform: `scale(${q})`, boxShadow: "0 4px 14px rgba(var(--a),.5)",
                    }}>{i + 1}</div>
                    <Reveal delay={st} style={{flex: 1}}>
                      <div style={{fontWeight: 800, fontSize: fitSize(txt, isLand ? 36 : 40, isLand ? 1080 : 740), color: C.cream, lineHeight: 1.2}}>{txt}</div>
                    </Reveal>
                  </div>
                );
              })}
            </div>
          </div>
        </Glass>
      </Scene>
    </>
  );
};

const Solid: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const isLand = width > height;
  const bg = interpolate(f, [0, 6], [0, 1], clamp);
  const gx = (f * 0.6) % 120;
  const gy = (f * 0.4) % 120;
  const lines: string[] = wrapLines(o.lines || [], isLand ? 28 : 18);
  const maxLen = Math.max(...lines.map((l) => l.replace("*", "").length), 1);
  const uw = interpolate(f, [10 + lines.length * 5, 22 + lines.length * 5], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <Scene dur={dur} moving={false}>
      <AbsoluteFill style={{background: `radial-gradient(circle at 50% 42%, ${C.mid} 0%, ${C.dark} 85%)`, opacity: bg}}>
        <div style={{
          position: "absolute", left: -120, top: -120, right: -120, bottom: -120,
          backgroundImage: "linear-gradient(rgba(var(--c),.07) 2px, transparent 2px), linear-gradient(90deg, rgba(var(--c),.07) 2px, transparent 2px)",
          backgroundSize: "120px 120px", transform: `translate(${gx}px, ${gy}px)`,
        }} />
        <AbsoluteFill style={{background: "radial-gradient(ellipse at 50% 45%, rgba(0,0,0,0) 25%, rgba(0,0,0,.78) 100%)"}} />
        {Array.from({length: 24}).map((_, i) => {
          const x = random("x" + i) * width;
          const y0 = random("y" + i) * height;
          const sp2 = 0.5 + random("s" + i) * 1.4;
          const y = (((y0 - f * sp2 * 3.5) % height) + height) % height;
          const r = 2 + random("r" + i) * 4;
          return <div key={i} style={{
            position: "absolute", left: x, top: y, width: r * 2, height: r * 2, borderRadius: r,
            background: C.orange, opacity: 0.2 + random("o" + i) * 0.4,
          }} />;
        })}
        <div style={{
          position: "absolute", left: isLand ? 240 : 70, right: isLand ? 240 : 70, top: isLand ? 220 : 420, height: isLand ? 500 : 540, borderRadius: 30,
          background: "rgba(0,0,0,0.38)", border: "1px solid rgba(255,255,255,0.22)",
          boxShadow: "0 30px 80px rgba(0,0,0,0.5)",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          padding: "30px 36px", boxSizing: "border-box",
        }}>
          {lines.map((l, i) => {
            const accent = l.startsWith("*");
            const t = l.replace(/^\*/, "");
            return (
              <Reveal key={i} delay={6 + i * 5} style={{textAlign: "center"}}>
                <div style={{
                  fontFamily: accent ? F_SERIF : F_DISPLAY, fontStyle: accent ? "italic" : "normal",
                  fontWeight: accent ? 700 : 800, fontSize: fitSize(t, accent ? 76 : 70, isLand ? 1240 : 820),
                  color: accent ? C.orange : C.cream, lineHeight: 1.25,
                }}>{t}</div>
              </Reveal>
            );
          })}
          <div style={{
            marginTop: 22, height: 6, borderRadius: 3,
            background: `linear-gradient(90deg, ${C.orange}, ${C.cream})`,
            width: Math.min(isLand ? 960 : 720, maxLen * 48) * uw,
          }} />
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

const hasWord = (name: any, words: string[]) => {
  const toks = String(name || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  return toks.some((t) => words.includes(t));
};

const IconArt: React.FC<any> = ({name, draw, f}) => {
  const C = useC();
  const L = 300;
  const dash = {strokeDasharray: L, strokeDashoffset: L * (1 - draw)};
  const o = C.orange, c = C.cream, d = C.dark;
  const flicker = 1 + Math.sin(f * 0.45) * 0.06;

  if (hasWord(name, ["api", "bakar", "panas", "kebakaran"])) return (<g>
    <circle r={115} fill="rgba(224,138,46,0.18)" />
    <g transform={`scale(${flicker})`}>
      <path d="M 0 -105 C 55 -55, 85 -5, 65 55 C 52 92, 22 105, 0 105 C -28 105, -62 88, -68 48 C -74 8, -42 -28, -22 -48 C -25 -22, -12 -10, -4 -22 C -12 -52, -8 -82, 0 -105 Z" fill={o} />
      <path d="M 0 -42 C 28 -12, 40 20, 28 56 C 20 78, 8 86, 0 86 C -14 86, -30 75, -32 52 C -35 28, -16 6, 0 -42 Z" fill={c} />
    </g>
  </g>);

  if (hasWord(name, ["bahaya", "awas", "peringatan", "seru"])) return (<g>
    <polygon points="0,-105 108,82 -108,82" fill={o} stroke={c} strokeWidth={10} strokeLinejoin="round" />
    <rect x={-11} y={-42} width={22} height={64} rx={11} fill={d} />
    <circle cx={0} cy={46} r={13} fill={d} />
  </g>);

  if (hasWord(name, ["fakta", "perisai", "polisi", "aman"])) return (<g>
    <path d="M 0 -105 L 88 -68 L 88 12 C 88 68, 45 98, 0 112 C -45 98, -88 68, -88 12 L -88 -68 Z" fill={o} />
    <path d="M -36 4 L -10 32 L 42 -28" fill="none" stroke={c} strokeWidth={18} strokeLinecap="round" strokeLinejoin="round" style={dash} />
  </g>);

  if (hasWord(name, ["berita", "viral", "heboh", "hoaks", "suara"])) return (<g>
    <circle r={110} fill="rgba(224,138,46,0.16)" />
    <polygon points="-75,-24 -18,-24 45,-68 45,68 -18,24 -75,24" fill={c} />
    <rect x={-55} y={24} width={22} height={48} rx={8} fill={o} />
    <path d="M 66 -34 A 46 46 0 0 1 66 34" fill="none" stroke={o} strokeWidth={12} strokeLinecap="round" style={dash} />
    <path d="M 88 -56 A 76 76 0 0 1 88 56" fill="none" stroke={c} strokeWidth={10} strokeLinecap="round" style={dash} />
  </g>);

  if (hasWord(name, ["waktu", "jam", "malam"])) return (<g>
    <circle r={98} fill={c} stroke={o} strokeWidth={16} />
    <line x1={0} y1={0} x2={0} y2={-52} stroke={d} strokeWidth={14} strokeLinecap="round" />
    <line x1={0} y1={0} x2={42} y2={18} stroke={o} strokeWidth={12} strokeLinecap="round" style={dash} />
    <circle r={12} fill={d} />
  </g>);

  const key = String(name || "").toLowerCase();
  if (key === "silang") return (<g>
    <circle r={98} fill={o} />
    <line x1={-40} y1={-40} x2={40} y2={40} stroke={c} strokeWidth={20} strokeLinecap="round" style={dash} />
    <line x1={-40} y1={40} x2={40} y2={-40} stroke={c} strokeWidth={20} strokeLinecap="round" style={dash} />
  </g>);
  if (key === "centang") return (<g>
    <circle r={98} fill={o} />
    <path d="M -45 0 L -10 38 L 50 -35" fill="none" stroke={c} strokeWidth={20} strokeLinecap="round" strokeLinejoin="round" style={dash} />
  </g>);
  return (<g>
    <circle r={96} fill={o} />
    <polygon points="0,-54 16,-16 56,-16 24,10 36,48 0,24 -36,48 -24,10 -56,-16 -16,-16" fill={c} />
  </g>);
};

const Icon: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const pIn = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR})));
  const pop = spring({frame: f - 3, fps, config: {damping: 10, stiffness: 175, mass: 0.7}});
  const draw = interpolate(f, [6, 20], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const ring = interpolate(f, [6, 24], [0, 1], clamp);
  const floatY = Math.sin(f * 0.16) * 8;
  const label = up(o.label);
  const cx = width / 2;
  const cy = isLand ? 410 : 635;
  return (
    <Scene dur={dur} moving={false}>
      <Glass o={o} x={isLand ? 460 : 90} y={isLand ? 210 : 410} w={isLand ? 1000 : 900} h={isLand ? 520 : 590} radius={34} style={{opacity: pIn, transform: `scale(${0.9 + 0.1 * pIn})`}} />
      <svg width={width} height={height} style={{position: "absolute", left: 0, top: 0}}>
        <g transform={`translate(${cx} ${cy + floatY}) scale(${Math.max(0, pop)})`}>
          <IconArt name={o.icon} draw={draw} f={f} />
        </g>
        <circle cx={cx} cy={cy} r={95 + ring * 185} fill="none" stroke={C.orange}
          strokeWidth={6 * (1 - ring) + 1} opacity={(1 - ring) * (f >= 6 ? 1 : 0)} />
      </svg>
      <div style={{position: "absolute", left: isLand ? 490 : 110, right: isLand ? 490 : 110, top: isLand ? 600 : 836, textAlign: "center"}}>
        <Reveal delay={10}>
          <div style={{fontWeight: 800, fontSize: fitSize(label, 40, isLand ? 920 : 820), color: C.cream}}>{label}</div>
        </Reveal>
      </div>
    </Scene>
  );
};

const QMark: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const s = spring({frame: f, fps, config: {damping: 9, stiffness: 170, mass: 0.7}});
  const wob = Math.sin(f / 5) * 3 * Math.max(0, 1 - f / 30);
  const pill = Math.min(1, Math.max(0, spring({frame: f - 8, fps, config: SPR})));
  const label = up(o.label);
  const glow = 26 + Math.sin(f / 4) * 12;
  return (
    <Scene dur={dur}>
      <div style={{
        position: "absolute", left: 0, right: 0, top: isLand ? 160 : 330, textAlign: "center",
        fontFamily: F_SERIF, fontStyle: "italic", fontWeight: 700,
        fontSize: isLand ? 310 : 380, color: C.orange, lineHeight: 1,
        transform: `scale(${Math.max(0, s)}) rotate(${wob}deg)`,
        WebkitTextStroke: `8px ${C.black}`, paintOrder: "stroke fill",
        textShadow: `0 0 ${glow}px rgba(var(--a),.7)`,
      } as any}>?</div>
      <Glass o={o} x={isLand ? 360 : 90} y={isLand ? 540 : 800} w={isLand ? 1200 : 900} radius={44} style={{opacity: pill, transform: `translateY(${(1 - pill) * 40}px)`}}>
        <div style={{padding: "14px 40px 18px", textAlign: "center"}}>
          <Reveal delay={8}>
            <div style={{fontWeight: 800, fontSize: fitSize(label, 40, isLand ? 1100 : 800), color: C.cream, lineHeight: 1.25}}>{label}</div>
          </Reveal>
        </div>
      </Glass>
    </Scene>
  );
};

let GEO_PROMISE: Promise<any> | null = null;
const loadGeo = (): Promise<any> => {
  if (GEO_PROMISE) return GEO_PROMISE;
  GEO_PROMISE = fetch(staticFile("indonesia-prov.geojson"))
    .then((r) => (r.ok ? r.json() : null))
    .then((p: any) => ({prov: p && Array.isArray(p.features) ? p.features : []}))
    .catch(() => ({prov: []}));
  return GEO_PROMISE;
};

const useGeo = () => {
  const [geo, setGeo] = useState<any>(null);
  const [handle] = useState(() => delayRender("geo", {timeoutInMilliseconds: 60000}));
  useEffect(() => {
    let alive = true;
    loadGeo().then((g) => { if (alive) setGeo(g); }).finally(() => continueRender(handle));
    return () => { alive = false; };
  }, [handle]);
  return geo;
};

const MapView: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const MAP_W = isLand ? 1160 : 860;
  const MAP_H = isLand ? 520 : 560;
  const geo = useGeo();
  const pin: [number, number] = Array.isArray(o.pin) && o.pin.length === 2 ? [Number(o.pin[0]), Number(o.pin[1])] : [106.25, -6.62];
  const pinKey = pin.join(",");

  const base: any = useMemo(() => {
    const p: any = geoMercator();
    const feats = geo && geo.prov ? geo.prov : [];
    if (feats.length) p.fitExtent([[28, 28], [MAP_W - 28, MAP_H - 28]], {type: "FeatureCollection", features: feats} as any);
    else p.center([118, -2]).scale(980).translate([MAP_W / 2, MAP_H / 2]);
    return p;
  }, [geo, MAP_W, MAP_H]);

  const built = useMemo(() => {
    const gp: any = geoPath(base);
    if (gp.digits) gp.digits(1);
    const feats = geo && geo.prov ? geo.prov : [];
    const shapes = feats.map((ft: any) => {
      let hit = false;
      try { hit = geoContains(ft, pin); } catch (e) { hit = false; }
      let bb: any = [[0, 0], [1, 1]];
      try { bb = gp.bounds(ft); } catch (e) { bb = [[0, 0], [1, 1]]; }
      return {d: gp(ft) || "", hit, bb};
    });
    let grat = "";
    try { grat = gp(geoGraticule().step([5, 5])()) || ""; } catch (e) { grat = ""; }
    return {shapes, grat};
  }, [geo, base, pinKey]);

  const P: any = base(pin) || [MAP_W / 2, MAP_H / 2];
  const C0: any = [MAP_W / 2, MAP_H / 2];
  const hitS: any = built.shapes.find((s: any) => s.hit);
  let k1 = o.zoom ? Number(o.zoom) / 5 : 6;
  if (hitS) {
    const bw = Math.max(1, hitS.bb[1][0] - hitS.bb[0][0]);
    const bh = Math.max(1, hitS.bb[1][1] - hitS.bb[0][1]);
    k1 = Math.min((MAP_W * 0.62) / bw, (MAP_H * 0.62) / bh);
  }
  k1 = Math.max(2, Math.min(16, k1));

  const flyEnd = Math.max(24, Math.round(dur * 0.6));
  const w = interpolate(f, [6, flyEnd], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const wc = 1 - Math.pow(1 - w, 2);
  const k = Math.exp(w * Math.log(k1) - 0.22 * Math.sin(Math.PI * w));
  const cx = C0[0] + (P[0] - C0[0]) * wc;
  const cy = C0[1] + (P[1] - C0[1]) * wc;
  const tx = MAP_W / 2 + (MAP_W * 0.42 - MAP_W / 2) * w;
  const ty = MAP_H / 2 + (MAP_H * 0.60 - MAP_H / 2) * w;
  const sx = tx + (P[0] - cx) * k;
  const sy = ty + (P[1] - cy) * k;

  const arrive = flyEnd;
  const pinOp = interpolate(f, [arrive - 10, arrive], [0, 1], clamp);
  const hitPulse = 0.22 + 0.08 * Math.sin(f / 5);

  const labelTxt = up(o.label);
  const labelLines = wrapLines([labelTxt], 13).slice(0, 2).map((l) => l.replace(/^\*/, ""));
  const subLines = o.sub ? wrapLines([titleCase(o.sub)], 24).slice(0, 2).map((l) => l.replace(/^\*/, "")) : [];
  const boxW = 330;
  const boxH = 26 + labelLines.length * 40 + (subLines.length ? 10 + subLines.length * 33 : 0) + 22;
  const L = 64, M = 16;
  const dirs: any[] = [[1, -1], [-1, -1], [1, 1], [-1, 1]];
  let pick: any = null;
  for (const [dx, dy] of dirs) {
    const ex = sx + dx * L, ey = sy + dy * L;
    const bx = dx > 0 ? ex : ex - boxW;
    const by = ey - boxH / 2;
    if (bx >= M && bx + boxW <= MAP_W - M && by >= M && by + boxH <= MAP_H - M) { pick = {dx, dy, ex, ey, bx, by}; break; }
  }
  if (!pick) {
    const dx = sx < MAP_W / 2 ? 1 : -1, dy = sy < MAP_H / 2 ? 1 : -1;
    const ex = sx + dx * L, ey = sy + dy * L;
    const bx0 = dx > 0 ? ex : ex - boxW;
    pick = {dx, dy, ex, ey,
      bx: Math.max(M, Math.min(MAP_W - M - boxW, bx0)),
      by: Math.max(M, Math.min(MAP_H - M - boxH, ey - boxH / 2))};
  }
  const lp = interpolate(f, [arrive, arrive + 10], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const bp = Math.min(1, Math.max(0, spring({frame: f - arrive - 6, fps, config: SPR})));
  const coord = `${Math.abs(pin[1]).toFixed(2)}°${pin[1] < 0 ? "S" : "N"}  ${Math.abs(pin[0]).toFixed(2)}°${pin[0] < 0 ? "W" : "E"}`;
  const cardIn = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR})));
  const lineLen = Math.sqrt(2) * L;

  return (
    <>
      <Dim dur={dur} />
      <Scene dur={dur} moving={false}>
        <Glass o={o} x={isLand ? 340 : 70} y={isLand ? 160 : 330} w={isLand ? 1240 : 940} h={isLand ? 650 : 690} radius={30} style={{opacity: cardIn, transform: `translateY(${(1 - cardIn) * 50}px)`}}>
          <div style={{position: "absolute", left: 40, right: 40, top: 22, height: 48, display: "flex", alignItems: "center", justifyContent: "space-between"}}>
            <div style={{display: "flex", alignItems: "center", gap: 12}}>
              <div style={{width: 12, height: 12, borderRadius: 6, background: C.orange, boxShadow: `0 0 12px ${C.orange}`}} />
              <Reveal delay={2}><div style={{fontFamily: F_COND, fontWeight: 700, fontSize: 28, letterSpacing: 5, color: C.orange}}>LOKASI</div></Reveal>
            </div>
            <div style={{fontFamily: F_COND, fontWeight: 700, fontSize: 26, letterSpacing: 2, color: C.cream, opacity: pinOp}}>{coord}</div>
          </div>
          <div style={{position: "absolute", left: 40, top: 84, width: MAP_W, height: MAP_H, borderRadius: 18, overflow: "hidden", background: C.dark, border: "1px solid rgba(255,255,255,0.16)"}}>
            <svg width={MAP_W} height={MAP_H} viewBox={`0 0 ${MAP_W} ${MAP_H}`} style={{position: "absolute", left: 0, top: 0}}>
              <defs>
                <radialGradient id="sea" cx="50%" cy="50%" r="80%">
                  <stop offset="0%" stopColor={C.mid} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={C.dark} stopOpacity={1} />
                </radialGradient>
                <radialGradient id="mvg" cx="50%" cy="50%" r="75%">
                  <stop offset="55%" stopColor="#000" stopOpacity={0} />
                  <stop offset="100%" stopColor="#000" stopOpacity={0.55} />
                </radialGradient>
              </defs>
              <rect width={MAP_W} height={MAP_H} fill="url(#sea)" />
              <g transform={`translate(${tx} ${ty}) scale(${k}) translate(${-cx} ${-cy})`}>
                {built.grat ? (
                  <path d={built.grat} fill="none" stroke={C.cream} strokeOpacity={0.09} strokeWidth={1} vectorEffect="non-scaling-stroke" />
                ) : null}
                {built.shapes.map((s: any, i: number) => (
                  <path key={"h" + i} d={s.d} fill="none" stroke={C.cream} strokeOpacity={0.1} strokeWidth={7}
                    strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
                ))}
                {built.shapes.map((s: any, i: number) => (
                  <path key={"p" + i} d={s.d}
                    fill={s.hit ? C.orange : C.mid} fillOpacity={s.hit ? hitPulse + 0.12 : 0.92}
                    stroke={s.hit ? C.orange : C.cream} strokeOpacity={s.hit ? 1 : 0.42}
                    strokeWidth={s.hit ? 2.4 : 0.9} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
                ))}
              </g>
              <rect width={MAP_W} height={MAP_H} fill="url(#mvg)" />
              <g opacity={pinOp}>
                {[0, 1, 2].map((i) => {
                  const q = ((f + i * 12) % 36) / 36;
                  return <circle key={i} cx={sx} cy={sy} r={10 + q * 62} fill="none" stroke={C.orange} strokeWidth={3} opacity={(1 - q) * 0.95} />;
                })}
                <circle cx={sx} cy={sy} r={9} fill={C.orange} stroke={C.cream} strokeWidth={3} />
              </g>
              <g opacity={pinOp > 0 ? 1 : 0}>
                <line x1={sx + pick.dx * 12} y1={sy + pick.dy * 12} x2={pick.ex} y2={pick.ey}
                  stroke={C.cream} strokeWidth={3} strokeLinecap="round"
                  strokeDasharray={lineLen} strokeDashoffset={lineLen * (1 - lp)} />
                <circle cx={pick.ex} cy={pick.ey} r={6 * lp} fill={C.orange} stroke={C.cream} strokeWidth={2} />
              </g>
              <g stroke={C.orange} strokeWidth={4} fill="none" opacity={0.9}>
                <path d="M 14 44 L 14 14 L 44 14" /><path d={`M ${MAP_W - 44} 14 L ${MAP_W - 14} 14 L ${MAP_W - 14} 44`} />
                <path d={`M 14 ${MAP_H - 44} L 14 ${MAP_H - 14} L 44 ${MAP_H - 14}`} />
                <path d={`M ${MAP_W - 14} ${MAP_H - 44} L ${MAP_W - 14} ${MAP_H - 14} L ${MAP_W - 44} ${MAP_H - 14}`} />
              </g>
            </svg>
            <div style={{
              position: "absolute", left: pick.bx, top: pick.by, width: boxW, height: boxH, boxSizing: "border-box",
              borderRadius: 14, background: "rgba(8,12,10,0.80)", border: "1px solid rgba(255,255,255,0.24)",
              borderLeft: `6px solid ${C.orange}`, boxShadow: "0 14px 34px rgba(0,0,0,0.5)",
              padding: "13px 18px 11px 18px", opacity: bp > 0 ? 1 : 0,
              clipPath: pick.dx > 0 ? `inset(0 ${(1 - bp) * 100}% 0 0)` : `inset(0 0 0 ${(1 - bp) * 100}%)`,
            } as any}>
              {labelLines.map((l, i) => (
                <div key={i} style={{fontFamily: F_DISPLAY, fontWeight: 800, fontSize: 32, lineHeight: "40px", color: C.cream, whiteSpace: "nowrap"}}>
                  {i === 0 && labelLines.length === 1 ? <MarkedText text={l} delay={arrive + 10} color={C.orange} ink={C.black} /> : l}
                </div>
              ))}
              {subLines.length ? (
                <div style={{marginTop: 10}}>
                  {subLines.map((l, i) => (
                    <div key={i} style={{fontFamily: F_SERIF, fontStyle: "italic", fontWeight: 700, fontSize: 26, lineHeight: "33px", color: C.orange, whiteSpace: "nowrap"}}>{l}</div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </Glass>
      </Scene>
    </>
  );
};

const Caps: React.FC<any> = ({cue, dur, fps}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const isLand = width > height;
  const out = interpolate(f, [dur - 4, dur], [1, 0], clamp);
  const inn = interpolate(f, [0, 4], [0, 1], clamp);
  const longest = Math.max(...cue.lines.map((ln: any[]) => ln.map((x: any) => x.w).join(" ").length), 1);
  const fs = Math.max(isLand ? 30 : 34, Math.min(isLand ? 44 : 56, (isLand ? 1400 : 840) / (longest * 0.68)));
  return (
    <AbsoluteFill style={{fontFamily: F_DISPLAY, opacity: out * inn}}>
      <div style={{position: "absolute", left: "8%", width: "84%", bottom: isLand ? "7%" : "24%", display: "flex", justifyContent: "center"}}>
        <div style={{
          maxWidth: "100%", boxSizing: "border-box", padding: "12px 28px 16px", borderRadius: 24,
          background: "rgba(8,10,9,0.68)", border: "1px solid rgba(255,255,255,0.14)",
          boxShadow: "0 12px 34px rgba(0,0,0,0.45)",
          display: "flex", flexDirection: "column", alignItems: "center", gap: 4, textAlign: "center",
        }}>
          {cue.lines.map((ln: any[], li: number) => (
            <div key={li} style={{display: "flex", justifyContent: "center", gap: fs * 0.3, whiteSpace: "nowrap"}}>
              {ln.map((wd: any, wi: number) => {
                const st = Math.round((wd.t0 - cue.s) * fps);
                const en = Math.round((wd.t1 - cue.s) * fps);
                const sp = spring({frame: f - st, fps, config: {damping: 12, stiffness: 230, mass: 0.5}});
                const live = f >= st && f < en;
                const sc = interpolate(Math.min(sp, 1.15), [0, 1], [0.6, 1]) * (wd.kw ? 1.05 : 1) * (live ? 1.03 : 1);
                const rise = (1 - Math.min(sp, 1)) * 18;
                return (
                  <div key={wi} style={{
                    fontWeight: 800, fontSize: fs, lineHeight: 1.25,
                    color: wd.kw ? C.orange : "#FFFFFF",
                    opacity: f >= st ? Math.min(1, sp * 2.5) : 0,
                    transform: `translateY(${rise}px) scale(${sc})`,
                    textShadow: wd.kw ? "0 0 22px rgba(var(--a),.6)" : "0 2px 8px rgba(0,0,0,0.5)",
                  } as any}>{wd.w}</div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const REG: any = {
  big: Big, lower: Lower, list: List, quote: List, solid: Solid,
  icon: Icon, qmark: QMark, map: MapView,
};

export const Overlay: React.FC<any> = ({overlays = [], fps = 30, images = [], captions = [], theme = null, glassSrc = null}) => {
  const C = useMemo(() => ({...DEFAULT_C, ...(theme && theme.hex ? theme.hex : {})}), [theme]);
  const vars: any = theme && theme.vars ? theme.vars : {};
  const cfg = useMemo(() => ({glass: glassSrc}), [glassSrc]);
  return (
    <ThemeCtx.Provider value={C}>
      <CfgCtx.Provider value={cfg}>
        <AbsoluteFill style={vars}>
          {overlays.map((o: any, i: number) => {
            const from = Math.round(o.t0 * fps);
            const dur = Math.max(6, Math.round((o.t1 - o.t0) * fps));
            const Comp = REG[o.type] || Lower;
            return (
              <Sequence key={i} from={from} durationInFrames={dur}>
                <Comp o={o} dur={dur} images={images} />
              </Sequence>
            );
          })}
          {captions.map((c: any, i: number) => {
            const from = Math.round(c.s * fps);
            const dur = Math.max(4, Math.round((c.e - c.s) * fps));
            return (
              <Sequence key={"c" + i} from={from} durationInFrames={dur}>
                <Caps cue={c} dur={dur} fps={fps} />
              </Sequence>
            );
          })}
        </AbsoluteFill>
      </CfgCtx.Provider>
    </ThemeCtx.Provider>
  );
};
""",
}


def prepare_remotion_project(remo_dir: str = "/tmp/remotion_overlay"):
    for d in ("src", "public/produk", "out"):
        os.makedirs(f"{remo_dir}/{d}", exist_ok=True)
    for rel, txt in REMO_FILES.items():
        with open(f"{remo_dir}/{rel}", "w", encoding="utf-8") as fh:
            fh.write(txt)


def download_geo_assets(remo_dir: str = "/tmp/remotion_overlay"):
    def _dl(urls, dest, check, label):
        if os.path.exists(dest) and os.path.getsize(dest) > 100:
            return True
        for u in urls:
            for attempt in range(1, 4):
                try:
                    data = urllib.request.urlopen(u, timeout=60).read()
                    obj = json.loads(data)
                    if not check(obj):
                        raise ValueError("struktur JSON tidak sesuai")
                    with open(dest, "wb") as fh:
                        fh.write(data)
                    print(f"[peta] {label} OK dari {u}")
                    return True
                except Exception as e:
                    print(f"[peta] {label} gagal ({u}, percobaan {attempt}): {e}")
                    time.sleep(1.5 * attempt)
        return False

    _dl(
        [
            f"https://raw.githubusercontent.com/ans-4175/peta-indonesia-geojson/{br}/indonesia-prov.geojson"
            for br in ("master", "main")
        ] + [
            "https://cdn.jsdelivr.net/gh/ans-4175/peta-indonesia-geojson@master/indonesia-prov.geojson",
        ],
        f"{remo_dir}/public/indonesia-prov.geojson",
        lambda o: isinstance(o.get("features"), list) and len(o["features"]) > 0,
        "peta provinsi",
    )
