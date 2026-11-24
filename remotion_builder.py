import os
import json
import time
import urllib.request

REMO_FILES = {
    "package.json": r"""{"name":"overlay","version":"1.0.0","private":true,"dependencies":{"remotion":"4.0.352","@remotion/cli":"4.0.352","react":"18.3.1","react-dom":"18.3.1","d3-geo":"3.1.1","topojson-client":"3.1.0"}}""",
    "tsconfig.json": r"""{"compilerOptions":{"target":"ES2020","module":"ESNext","moduleResolution":"Bundler","jsx":"react-jsx","strict":false,"skipLibCheck":true,"esModuleInterop":true,"noEmit":true},"include":["src"]}""",
    "src/index.ts": r"""import {registerRoot} from "remotion";
import {Root} from "./Root";
registerRoot(Root);
""",
    "src/Root.tsx": r"""import React from "react";
import {Composition} from "remotion";
import {Overlay} from "./Overlay";
export const Root: React.FC = () => {
  return (
    <Composition
      id="Overlay"
      component={Overlay as any}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={300}
      defaultProps={{overlays: [], fps: 30, images: [], durationInFrames: 300}}
      calculateMetadata={({props}: any) => ({
        durationInFrames: props.durationInFrames,
        fps: props.fps,
      })}
    />
  );
};
""",
    "src/Overlay.tsx": r"""import React, {createContext, useContext, useEffect, useMemo, useState} from "react";
import {
  AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig, spring, interpolate,
  Easing, random, staticFile, delayRender, continueRender,
} from "remotion";
import {geoMercator, geoPath, geoContains} from "d3-geo";
import {feature as topoFeature} from "topojson-client";

const DEFAULT_C = {
  green: "#1F3D2B", dark: "#162E20", cream: "#F2E8D5",
  orange: "#E08A2E", black: "#111111", mid: "#2D5C43",
};
const ThemeCtx = createContext<any>(DEFAULT_C);
const useC = () => useContext(ThemeCtx);

const FONT = "'DejaVu Sans', Verdana, sans-serif";
const clamp: any = {extrapolateLeft: "clamp", extrapolateRight: "clamp"};
const up = (s: any) => String(s || "").toUpperCase();
const fitSize = (txt: string, max: number, width: number) =>
  Math.max(24, Math.min(max, width / (Math.max(txt.length, 1) * 0.74)));

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
    <AbsoluteFill style={{opacity: out, transform: `translateY(${lift}px)`, fontFamily: FONT}}>
      {children}
    </AbsoluteFill>
  );
};

const Shine: React.FC<any> = ({start = 6, len = 18, alpha = 0.5}) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [start, start + len], [-0.3, 1.3], clamp);
  return (
    <div style={{position: "absolute", left: 0, top: 0, right: 0, bottom: 0, overflow: "hidden", pointerEvents: "none"}}>
      <div style={{
        position: "absolute", top: -200, bottom: -200, width: 150, left: `${p * 100}%`,
        transform: "skewX(-18deg)", filter: "blur(8px)",
        background: `linear-gradient(90deg, transparent, rgba(255,255,255,${alpha}), transparent)`,
      }} />
    </div>
  );
};

const Big: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const lines: string[] = wrapLines(o.lines || [], 16);
  const shakeAmp = (1 - interpolate(f, [0, 12], [0, 1], clamp)) * 8;
  const sx = Math.sin(f * 2.7) * shakeAmp;
  const sy = Math.cos(f * 3.1) * shakeAmp;
  const sBar = spring({frame: f - lines.length * 4, fps, config: {damping: 14, stiffness: 150}});
  const bgOp = interpolate(f, [0, 6], [0, 1], clamp);
  return (
    <Scene dur={dur}>
      <div style={{
        position: "absolute", left: 50, right: 50, top: 340, height: 680,
        borderRadius: 36, opacity: bgOp,
        background: "radial-gradient(ellipse at center, rgba(12,18,15,0.72) 0%, rgba(12,18,15,0.0) 75%)",
      }} />
      <div style={{
        position: "absolute", left: 60, right: 60, top: 340, height: 680,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        transform: `translate(${sx}px, ${sy}px)`,
      }}>
        {lines.map((l, i) => {
          const s = spring({frame: f - i * 4, fps, config: {damping: 10, stiffness: 190, mass: 0.6}});
          const sc = Math.min(1, s);
          const size = Math.min(108, Math.max(46, 860 / (Math.max(l.length, 4) * 0.74)));
          const isAccent = i === lines.length - 1 && lines.length > 1;
          return (
            <div key={i} style={{
              fontWeight: 800, fontSize: size, lineHeight: 1.16,
              color: isAccent ? C.orange : C.cream, letterSpacing: 1.5,
              textAlign: "center", maxWidth: 920,
              transform: `scale(${interpolate(s, [0, 1], [2.1, 1])}) rotate(${interpolate(s, [0, 1], [i % 2 ? 5 : -5, 0])}deg)`,
              opacity: Math.min(1, s * 2.2),
              filter: `blur(${(1 - sc) * 12}px)`,
              WebkitTextStroke: `8px ${C.black}`,
              paintOrder: "stroke fill",
              textShadow: `0 8px 34px rgba(0,0,0,0.85), 0 0 36px rgba(var(--a),${0.55 * sc})`,
            } as any}>{l}</div>
          );
        })}
        <div style={{
          marginTop: 24, height: 12, borderRadius: 6,
          background: `linear-gradient(90deg, ${C.orange}, ${C.cream})`,
          width: 520 * Math.min(1, sBar), boxShadow: "0 0 24px rgba(var(--a),.85)",
        }} />
      </div>
      <Shine start={6} alpha={0.35} />
    </Scene>
  );
};

const Lower: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = Math.min(1, spring({frame: f, fps, config: {damping: 15, stiffness: 150, mass: 0.8}}));
  const t = Math.min(1, spring({frame: f - 5, fps, config: {damping: 17, stiffness: 150}}));
  const prog = interpolate(f, [0, dur], [0, 100], clamp);
  const badge = up(o.badge || "SOROTAN FAKTA");
  return (
    <Scene dur={dur} moving={false}>
      <div style={{
        position: "absolute", left: 60, right: 60, top: 880,
        transform: `translateX(${(1 - s) * -70}px) scale(${0.94 + 0.06 * s})`,
        opacity: Math.min(1, s * 1.6),
      }}>
        <div style={{
          position: "relative", borderRadius: 24, overflow: "hidden",
          background: "linear-gradient(135deg, rgba(var(--d),.94) 0%, rgba(var(--g),.90) 100%)",
          border: `2px solid rgba(var(--a),.75)`,
          boxShadow: "0 20px 50px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.18)",
          padding: "18px 28px 22px 28px",
        }}>
          <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 10}}>
            <div style={{
              width: 12, height: 12, borderRadius: 6, background: C.orange,
              boxShadow: `0 0 12px ${C.orange}`,
            }} />
            <div style={{fontWeight: 800, fontSize: 22, letterSpacing: 2, color: C.orange}}>{badge}</div>
          </div>
          <div style={{overflow: "hidden"}}>
            <div style={{
              fontWeight: 800, fontSize: fitSize(up(o.text), 42, 860), color: C.cream,
              lineHeight: 1.28, transform: `translateY(${(1 - t) * 45}px)`,
            }}>{up(o.text)}</div>
          </div>
          <div style={{
            position: "absolute", left: 0, bottom: 0, height: 6,
            width: `${prog}%`, background: `linear-gradient(90deg, ${C.orange}, ${C.cream})`,
          }} />
          <Shine start={8} alpha={0.35} />
        </div>
      </div>
    </Scene>
  );
};

const List: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items: any[] = o.items || [];
  const n = Math.max(items.length, 1);
  const h = Math.min(440, 96 + 98 * n);
  const top = 1040 - h;
  const p = Math.min(1, spring({frame: f, fps, config: {damping: 15, stiffness: 150}}));
  return (
    <Scene dur={dur}>
      <div style={{
        position: "absolute", left: 60, right: 60, top, height: h,
        background: "linear-gradient(145deg, rgba(var(--d),.95) 0%, rgba(var(--g),.92) 100%)",
        borderRadius: 26, overflow: "hidden",
        border: `2px solid rgba(var(--a),.7)`,
        boxShadow: "0 24px 60px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.16)",
        transform: `translateY(${(1 - p) * 50}px) scale(${0.95 + 0.05 * p})`,
        opacity: Math.min(1, p * 1.5),
        padding: "22px 30px", boxSizing: "border-box",
        display: "flex", flexDirection: "column", justifyContent: "space-between",
      }}>
        <div style={{
          fontWeight: 800, fontSize: 22, letterSpacing: 2.2, color: C.orange,
          borderBottom: "1px solid rgba(255,255,255,0.12)", paddingBottom: 10,
        }}>{up(o.title || "RINGKASAN FAKTA")}</div>
        {items.map((it, i) => {
          const st = Math.round((it._t || i * 0.45) * fps);
          const q = Math.min(1, spring({frame: f - st, fps, config: {damping: 12, stiffness: 200, mass: 0.6}}));
          const txt = up(it.text);
          return (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 18,
              opacity: f >= st ? Math.min(1, q * 2) : 0,
              transform: `translateX(${(1 - q) * 50}px)`,
            }}>
              <div style={{
                minWidth: 38, height: 38, borderRadius: 12,
                background: C.orange, color: C.black,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontWeight: 800, fontSize: 22, transform: `scale(${q})`,
                boxShadow: "0 4px 14px rgba(var(--a),.5)",
              }}>{i + 1}</div>
              <div style={{
                fontWeight: 800, fontSize: fitSize(txt, 38, 760), color: C.cream, lineHeight: 1.2,
              }}>{txt}</div>
            </div>
          );
        })}
        <Shine start={10} alpha={0.28} />
      </div>
    </Scene>
  );
};

const Solid: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const bg = interpolate(f, [0, 6], [0, 1], clamp);
  const gx = (f * 0.6) % 120;
  const gy = (f * 0.4) % 120;
  const lines: string[] = wrapLines(o.lines || [], 18);
  const maxLen = Math.max(...lines.map((l) => l.replace("*", "").length), 1);
  const uw = interpolate(f, [10 + lines.length * 5, 22 + lines.length * 5], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <Scene dur={dur} moving={false}>
      <AbsoluteFill style={{background: `radial-gradient(circle at 50% 42%, ${C.mid} 0%, ${C.dark} 85%)`, opacity: bg}}>
        <div style={{
          position: "absolute", left: -120, top: -120, right: -120, bottom: -120,
          backgroundImage: "linear-gradient(rgba(var(--c),.08) 2px, transparent 2px), linear-gradient(90deg, rgba(var(--c),.08) 2px, transparent 2px)",
          backgroundSize: "120px 120px", transform: `translate(${gx}px, ${gy}px)`,
        }} />
        <AbsoluteFill style={{background: "radial-gradient(ellipse at 50% 45%, rgba(0,0,0,0) 25%, rgba(0,0,0,.78) 100%)"}} />
        {Array.from({length: 28}).map((_, i) => {
          const x = random("x" + i) * 1080;
          const y0 = random("y" + i) * 1920;
          const sp2 = 0.5 + random("s" + i) * 1.4;
          const y = (((y0 - f * sp2 * 3.5) % 1920) + 1920) % 1920;
          const r = 2 + random("r" + i) * 5;
          return <div key={i} style={{
            position: "absolute", left: x, top: y, width: r * 2, height: r * 2, borderRadius: r,
            background: C.orange, opacity: 0.25 + random("o" + i) * 0.45,
            boxShadow: `0 0 10px ${C.orange}`,
          }} />;
        })}
        <div style={{
          position: "absolute", left: 70, right: 70, top: 420, height: 540,
          borderRadius: 34, background: "rgba(0,0,0,0.38)",
          border: `2px solid rgba(var(--a),.55)`,
          boxShadow: "0 30px 80px rgba(0,0,0,0.5)",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          padding: "30px 36px", boxSizing: "border-box",
        }}>
          {lines.map((l, i) => {
            const orange = l.startsWith("*");
            const t = l.replace(/^\*/, "");
            const s = Math.min(1, spring({frame: f - 6 - i * 5, fps, config: {damping: 14, stiffness: 160}}));
            return (
              <div key={i} style={{overflow: "hidden", padding: "6px 0", textAlign: "center"}}>
                <div style={{
                  fontWeight: 800, fontSize: fitSize(t, 74, 820), color: orange ? C.orange : C.cream,
                  lineHeight: 1.25, transform: `translateY(${(1 - s) * 100}%)`, opacity: s,
                  textShadow: orange ? "0 0 28px rgba(var(--a),.65)" : "0 4px 16px rgba(0,0,0,0.6)",
                }}>{t}</div>
              </div>
            );
          })}
          <div style={{
            marginTop: 22, height: 10, borderRadius: 5,
            background: `linear-gradient(90deg, ${C.orange}, ${C.cream})`,
            width: Math.min(720, maxLen * 48) * uw, boxShadow: "0 0 22px rgba(var(--a),.8)",
          }} />
        </div>
        <Shine start={8} len={22} alpha={0.25} />
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
  const {fps} = useVideoConfig();
  const pIn = Math.min(1, spring({frame: f, fps, config: {damping: 14, stiffness: 150}}));
  const pop = spring({frame: f - 3, fps, config: {damping: 8, stiffness: 175, mass: 0.7}});
  const draw = interpolate(f, [6, 20], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const ring = interpolate(f, [6, 24], [0, 1], clamp);
  const floatY = Math.sin(f * 0.16) * 8;
  const label = up(o.label);
  const lbl = Math.min(1, spring({frame: f - 10, fps, config: {damping: 15, stiffness: 150}}));
  return (
    <Scene dur={dur} moving={false}>
      <AbsoluteFill style={{background: "rgba(var(--g),.52)", opacity: pIn}} />
      <div style={{
        position: "absolute", left: 90, right: 90, top: 410, height: 590, borderRadius: 34,
        background: "linear-gradient(155deg, rgba(var(--d),.95) 0%, rgba(var(--g),.92) 100%)",
        border: `3px solid rgba(var(--a),.8)`,
        boxShadow: "0 30px 70px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.2)",
        opacity: pIn, transform: `scale(${0.88 + 0.12 * pIn})`,
        transformOrigin: "50% 50%", overflow: "hidden",
      }}>
        <Shine start={8} len={20} alpha={0.3} />
      </div>
      <svg width={1080} height={1920} style={{position: "absolute", left: 0, top: 0}}>
        <g transform={`translate(540 ${635 + floatY}) scale(${Math.max(0, pop)})`}>
          <IconArt name={o.icon} draw={draw} f={f} />
        </g>
        <circle cx={540} cy={635} r={95 + ring * 185} fill="none" stroke={C.orange}
          strokeWidth={6 * (1 - ring) + 1} opacity={(1 - ring) * (f >= 6 ? 1 : 0)} />
      </svg>
      <div style={{
        position: "absolute", left: 110, right: 110, top: 840, textAlign: "center", overflow: "hidden", height: 90,
      }}>
        <div style={{
          fontWeight: 800, fontSize: fitSize(label, 42, 820), color: C.cream,
          transform: `translateY(${(1 - lbl) * 70}px)`,
        }}>{label}</div>
      </div>
    </Scene>
  );
};

const QMark: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f, fps, config: {damping: 7, stiffness: 170, mass: 0.7}});
  const wob = Math.sin(f / 5) * 4 * Math.max(0, 1 - f / 30);
  const pill = Math.min(1, spring({frame: f - 10, fps, config: {damping: 14, stiffness: 160}}));
  const label = up(o.label);
  const glow = 30 + Math.sin(f / 4) * 14;
  return (
    <Scene dur={dur}>
      <div style={{
        position: "absolute", left: 0, right: 0, top: 360, textAlign: "center", fontWeight: 800,
        fontSize: 330, color: C.orange, lineHeight: 1,
        transform: `scale(${Math.max(0, s)}) rotate(${wob}deg)`,
        WebkitTextStroke: `8px ${C.black}`, paintOrder: "stroke fill",
        textShadow: `0 0 ${glow}px rgba(var(--a),.75)`,
      } as any}>?</div>
      <div style={{
        position: "absolute", left: 60, right: 60, top: 810, display: "flex", justifyContent: "center",
        transform: `translateY(${(1 - pill) * 50}px)`, opacity: pill,
      }}>
        <div style={{
          fontWeight: 800, fontSize: fitSize(label, 42, 820), color: C.cream, padding: "16px 42px",
          borderRadius: 44, background: "rgba(var(--d),.94)", border: `3px solid ${C.orange}`,
          textAlign: "center", boxShadow: "0 16px 40px rgba(0,0,0,0.5)",
        }}>{label}</div>
      </div>
    </Scene>
  );
};

const CardFrame: React.FC<any> = ({dur, o, children}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f, fps, config: {damping: 13, stiffness: 120, mass: 0.9}});
  const sc = Math.min(1, s);
  const lab = Math.min(1, spring({frame: f - 12, fps, config: {damping: 15, stiffness: 150}}));
  const label = up(o.label);
  return (
    <Scene dur={dur} moving={false}>
      <div style={{position: "absolute", left: 100, top: 330, width: 880, height: 730, perspective: 1400}}>
        <div style={{
          position: "relative", width: "100%", height: "100%", borderRadius: 28, overflow: "hidden",
          background: "rgba(var(--g),.95)", border: `4px solid ${C.orange}`, boxSizing: "border-box",
          transform: `rotateY(${interpolate(s, [0, 1], [38, 0])}deg) translateY(${(1 - sc) * 80}px) scale(${0.9 + 0.1 * sc})`,
          opacity: Math.min(1, s * 2), boxShadow: "0 30px 80px rgba(0,0,0,.45)",
        }}>
          <div style={{position: "absolute", left: 26, top: 26, width: 820, height: 480, borderRadius: 18, overflow: "hidden", background: C.dark}}>
            {children}
          </div>
          <div style={{position: "absolute", left: 20, right: 20, top: 530, textAlign: "center", overflow: "hidden", height: 70}}>
            <div style={{
              fontWeight: 800, fontSize: fitSize(label, 42, 820), color: C.cream,
              transform: `translateY(${(1 - lab) * 70}px)`,
            }}>{label}</div>
          </div>
          {o.sub ? (
            <div style={{position: "absolute", left: 20, right: 20, top: 610, textAlign: "center", overflow: "hidden", height: 64}}>
              <div style={{
                fontWeight: 800, fontSize: fitSize(up(o.sub), 38, 820), color: C.orange,
                transform: `translateY(${(1 - Math.min(1, spring({frame: f - 18, fps, config: {damping: 15, stiffness: 150}}))) * 70}px)`,
              }}>{up(o.sub)}</div>
            </div>
          ) : null}
          <Shine start={14} len={22} alpha={0.4} />
        </div>
      </div>
    </Scene>
  );
};

let GEO_PROMISE: Promise<any> | null = null;
const loadGeo = (): Promise<any> => {
  if (GEO_PROMISE) return GEO_PROMISE;
  const ld = async (n: string) => {
    try {
      const r = await fetch(staticFile(n));
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  };
  GEO_PROMISE = Promise.all([ld("world.json"), ld("indonesia-prov.geojson")]).then(([w, p]: any) => {
    let countries: any[] = [];
    try {
      if (w && w.objects && w.objects.countries) countries = (topoFeature(w, w.objects.countries) as any).features;
    } catch (e) { countries = []; }
    return {countries, prov: p && p.features ? p.features : []};
  }).catch(() => ({countries: [], prov: []}));
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
  const geo = useGeo();
  const proj: any = useMemo(() => geoMercator().center([0, 15]).scale(118).translate([410, 240]), []);
  const pin: [number, number] = o.pin || [106.25, -6.62];
  const from: any = o.from || null;
  const pp = proj(pin);
  const fp = from ? proj([from[0], from[1]]) : null;
  const S = Math.max(4, o.zoom || 60);
  const w = interpolate(f, [6, Math.round(dur * 0.72)], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const sc = Math.pow(S, w);
  const tx = w * (410 - pp[0] * sc);
  const ty = w * (240 - pp[1] * sc);
  const shapes = useMemo(() => {
    if (!geo) return {c: [], p: []};
    const gp = geoPath(proj);
    const hasProv = geo.prov.length > 0;
    const c = geo.countries.map((ft: any) => {
      let hit = false;
      try { hit = geoContains(ft, pin); } catch (e) { hit = false; }
      return {d: gp(ft) || "", hit: hit && !hasProv};
    });
    const p = geo.prov.map((ft: any) => {
      let hit = false;
      try { hit = geoContains(ft, pin); } catch (e) { hit = false; }
      return {d: gp(ft) || "", hit};
    });
    return {c, p};
  }, [geo]);
  const grat: any[] = useMemo(() => {
    const g: any[] = [];
    for (let lon = -180; lon <= 180; lon += 15) g.push({a: proj([lon, -75]), b: proj([lon, 80])});
    for (let lat = -60; lat <= 80; lat += 15) g.push({a: proj([-180, lat]), b: proj([180, lat])});
    return g;
  }, []);
  const sx = pp[0] * sc + tx;
  const sy = pp[1] * sc + ty;
  const r = interpolate(w, [0.35, 0.95], [0, 1], clamp);
  let routeD = "", dot: any = null;
  if (fp) {
    const cx = (fp[0] + pp[0]) / 2, cy = (fp[1] + pp[1]) / 2 - 25 / S;
    routeD = `M ${fp[0]} ${fp[1]} Q ${cx} ${cy} ${pp[0]} ${pp[1]}`;
    const t = r;
    dot = [
      (1 - t) * (1 - t) * fp[0] + 2 * (1 - t) * t * cx + t * t * pp[0],
      (1 - t) * (1 - t) * fp[1] + 2 * (1 - t) * t * cy + t * t * pp[1],
    ];
  }
  const pinOp = interpolate(w, [0.75, 1], [0, 1], clamp);
  const provOp = interpolate(w, [0.35, 0.75], [0, 1], clamp);
  const hitPulse = 0.35 + 0.15 * Math.sin(f / 5);
  const coord = `${Math.abs(pin[1]).toFixed(2)}°${pin[1] < 0 ? "S" : "N"} ${Math.abs(pin[0]).toFixed(2)}°${pin[0] < 0 ? "W" : "E"}`;
  return (
    <CardFrame o={o} dur={dur}>
      <svg width={820} height={480} viewBox="0 0 820 480" style={{position: "absolute", left: 0, top: 0}}>
        <rect width={820} height={480} fill={C.dark} />
        <g transform={`translate(${tx} ${ty}) scale(${sc})`}>
          {grat.map((g, i) => (
            <line key={i} x1={g.a[0]} y1={g.a[1]} x2={g.b[0]} y2={g.b[1]}
              stroke={C.cream} strokeOpacity={0.07} strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          {shapes.c.map((p: any, i: number) => (
            <path key={"c" + i} d={p.d} fill={p.hit ? C.orange : C.mid} fillOpacity={p.hit ? hitPulse + 0.4 : 1}
              stroke={C.cream} strokeOpacity={0.4} strokeWidth={0.8} vectorEffect="non-scaling-stroke" />
          ))}
          <g opacity={provOp}>
            {shapes.p.map((p: any, i: number) => (
              <path key={"p" + i} d={p.d} fill={p.hit ? C.orange : C.mid} fillOpacity={p.hit ? hitPulse + 0.4 : 1}
                stroke={C.cream} strokeOpacity={0.45} strokeWidth={0.8} vectorEffect="non-scaling-stroke" />
            ))}
          </g>
          {fp ? (
            <g>
              <path d={routeD} fill="none" stroke={C.cream} strokeOpacity={0.25} strokeWidth={2}
                strokeDasharray="4 6" vectorEffect="non-scaling-stroke" />
              <path d={routeD} pathLength={1} fill="none" stroke={C.orange} strokeWidth={4}
                strokeLinecap="round" strokeDasharray={1} strokeDashoffset={1 - r}
                vectorEffect="non-scaling-stroke" />
              {dot ? <circle cx={dot[0]} cy={dot[1]} r={5 / sc} fill={C.cream} /> : null}
            </g>
          ) : null}
        </g>
        <g opacity={pinOp}>
          {[0, 1, 2].map((i) => {
            const k = ((f + i * 10) % 30) / 30;
            return <circle key={i} cx={sx} cy={sy} r={8 + k * 45} fill="none" stroke={C.orange}
              strokeWidth={3} opacity={1 - k} />;
          })}
          <circle cx={sx} cy={sy} r={10} fill={C.orange} stroke={C.cream} strokeWidth={3} />
        </g>
        <defs>
          <radialGradient id="vg" cx="50%" cy="50%" r="75%">
            <stop offset="55%" stopColor="#000" stopOpacity={0} />
            <stop offset="100%" stopColor="#000" stopOpacity={0.6} />
          </radialGradient>
        </defs>
        <rect width={820} height={480} fill="url(#vg)" />
        <g stroke={C.orange} strokeWidth={4} fill="none" opacity={0.9}>
          <path d="M 14 44 L 14 14 L 44 14" /><path d="M 776 14 L 806 14 L 806 44" />
          <path d="M 14 436 L 14 466 L 44 466" /><path d="M 806 436 L 806 466 L 776 466" />
        </g>
        <text x={28} y={452} fill={C.cream} fontSize={18} fontWeight={800} fontFamily={FONT}
          opacity={pinOp}>{coord}</text>
      </svg>
    </CardFrame>
  );
};

const Caps: React.FC<any> = ({cue, dur, fps}) => {
  const C = useC();
  const f = useCurrentFrame();
  const out = interpolate(f, [dur - 4, dur], [1, 0], clamp);
  return (
    <AbsoluteFill style={{fontFamily: FONT, opacity: out}}>
      <div style={{position: "absolute", left: 40, right: 40, bottom: 440, display: "flex", flexDirection: "column", alignItems: "center"}}>
        {cue.lines.map((ln: any[], li: number) => (
          <div key={li} style={{display: "flex", justifyContent: "center", gap: 24, whiteSpace: "nowrap", marginTop: li ? 16 : 0}}>
            {ln.map((wd: any, wi: number) => {
              const st = Math.round((wd.t0 - cue.s) * fps);
              const en = Math.round((wd.t1 - cue.s) * fps);
              const sp = spring({frame: f - st, fps, config: {damping: 11, stiffness: 230, mass: 0.5}});
              const live = f >= st && f < en;
              const sc = interpolate(Math.min(sp, 1.15), [0, 1], [0.5, 1]) * (wd.kw ? 1.05 : 1) * (live ? 1.03 : 1);
              const rise = (1 - Math.min(sp, 1)) * 24;
              return (
                <div key={wi} style={{
                  fontWeight: 800, fontSize: 54, color: wd.kw ? C.orange : "#FFFFFF",
                  opacity: f >= st ? Math.min(1, sp * 2.5) : 0,
                  transform: `translateY(${rise}px) scale(${sc})`,
                  WebkitTextStroke: `6px ${C.black}`, paintOrder: "stroke fill",
                  textShadow: wd.kw ? "0 0 28px rgba(var(--a),.7)" : "none",
                } as any}>{wd.w}</div>
              );
            })}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const REG: any = {
  big: Big, lower: Lower, list: List, quote: List, solid: Solid,
  icon: Icon, qmark: QMark, map: MapView,
};

export const Overlay: React.FC<any> = ({overlays = [], fps = 30, images = [], captions = [], theme = null}) => {
  const C = useMemo(() => ({...DEFAULT_C, ...(theme && theme.hex ? theme.hex : {})}), [theme]);
  const vars: any = theme && theme.vars ? theme.vars : {};
  return (
    <ThemeCtx.Provider value={C}>
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
            "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json",
            "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json",
        ],
        f"{remo_dir}/public/world.json",
        lambda o: "objects" in o and "countries" in o["objects"],
        "peta dunia",
    )
    _dl(
        [
            f"https://raw.githubusercontent.com/ans-4175/peta-indonesia-geojson/{br}/indonesia-prov.geojson"
            for br in ("master", "main")
        ],
        f"{remo_dir}/public/indonesia-prov.geojson",
        lambda o: isinstance(o.get("features"), list) and len(o["features"]) > 0,
        "peta provinsi",
    )
