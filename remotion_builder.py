import os

IS_LONG = os.environ.get("VIDEO_MODE", "").strip().upper() == "LONG"
OUT_W = 1920 if IS_LONG else 1080
OUT_H = 1080 if IS_LONG else 1920

REMO_FILES = {
    "package.json": r"""{"name":"overlay","version":"1.0.0","private":true,"dependencies":{"remotion":"4.0.352","@remotion/cli":"4.0.352","react":"18.3.1","react-dom":"18.3.1","@remotion/google-fonts":"4.0.352"}}""",
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
    "src/Overlay.tsx": r"""import React, {createContext, useContext, useMemo} from "react";
import {
  AbsoluteFill, Sequence, OffthreadVideo, useCurrentFrame, useVideoConfig, spring, interpolate,
  Easing, random, staticFile,
} from "remotion";
import {loadFont as loadMontserrat} from "@remotion/google-fonts/Montserrat";
import {loadFont as loadOswald} from "@remotion/google-fonts/Oswald";
import {loadFont as loadPlayfair} from "@remotion/google-fonts/PlayfairDisplay";

const mont: any = loadMontserrat("normal", {weights: ["600", "800"], subsets: ["latin"]});
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
const CfgCtx = createContext<any>({});

const clamp: any = {extrapolateLeft: "clamp", extrapolateRight: "clamp"};
const SPR = {damping: 18, stiffness: 220, mass: 0.7};
const up = (s: any) => String(s || "").toUpperCase();

// ZONA AMAN VERTIKAL: overlay berada di top..bottom, subtitle dipatok di `sub` agar tidak menumpuk.
const ZONE = (isLand: boolean) => ({
  top: isLand ? 90 : 300,
  bottom: isLand ? 800 : 1120,
  sub: isLand ? 996 : 1470,
});
const zoneMid = (isLand: boolean) => (ZONE(isLand).top + ZONE(isLand).bottom) / 2;

const clipWords = (s: string, n: number) => {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  if (t.length <= n) return t;
  const cut = t.slice(0, n);
  const i = cut.lastIndexOf(" ");
  return (i > 8 ? cut.slice(0, i) : cut).trim();
};

const SUB_SHADOW = "0 1px 1px rgba(0,0,0,0.95), 0 2px 4px rgba(0,0,0,0.85), 0 6px 18px rgba(0,0,0,0.65), 0 12px 36px rgba(0,0,0,0.45)";

const fitSize = (txt: string, max: number, width: number) =>
  Math.max(24, Math.min(max, width / (Math.max(txt.length, 1) * 0.78)));

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

const Panel: React.FC<any> = ({x, y, w, h, style = {}, children}) => {
  return (
    <div style={{
      position: "absolute", left: x, top: y, width: w, height: h,
      boxSizing: "border-box", background: "transparent", border: "none", boxShadow: "none",
      ...style,
    }}>
      {children}
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// EDITORIAL MOTION GRAPHIC COMPONENTS
// ─────────────────────────────────────────────────────────────────────────────

// 1. CoverStory — magazine investigative title layout
const CoverStory: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const Z = ZONE(isLand);

  // Thin framing rules animate in
  const ruleP = interpolate(f, [0, 14], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  // Global fade out
  const fadeOut = interpolate(f, [dur - 8, dur], [1, 0], clamp);

  const kicker = up(o.kicker || o.badge || "LAPORAN KHUSUS");
  const titleLines: string[] = wrapLines(o.lines || [up(o.title || o.text || "")], isLand ? 24 : 14).slice(0, 3);
  const sub = up(o.sub || "");

  const boxW = isLand ? 1480 : 920;
  const boxX = (width - boxW) / 2;
  const boxTop = isLand ? Z.top + 60 : Z.top + 80;

  const ruleW = boxW * ruleP;
  const ruleColor = C.orange;

  return (
    <AbsoluteFill style={{opacity: fadeOut, fontFamily: F_DISPLAY}}>
      {/* subtle dark vignette for readability */}
      <div style={{
        position: "absolute", left: 0, right: 0, top: 0, bottom: 0,
        background: "radial-gradient(ellipse at 50% 42%, rgba(0,0,0,0.28) 0%, rgba(0,0,0,0.52) 100%)",
      }} />

      {/* Top framing rule */}
      <div style={{
        position: "absolute", left: boxX, top: boxTop, height: 2,
        width: ruleW, background: `linear-gradient(90deg, ${ruleColor}, rgba(255,255,255,0.7), ${ruleColor})`,
        boxShadow: `0 0 10px rgba(var(--a),0.6)`,
      }} />

      {/* Kicker label */}
      <Reveal delay={4} style={{position: "absolute", left: boxX + 8, top: boxTop + 12}}>
        <div style={{
          fontFamily: F_COND, fontWeight: 700, fontSize: isLand ? 20 : 24,
          letterSpacing: 6, color: ruleColor,
          textShadow: "0 1px 6px rgba(0,0,0,0.9)",
        }}>{kicker}</div>
      </Reveal>

      {/* Main title lines — masked overflow: hidden reveal */}
      <div style={{
        position: "absolute", left: boxX, top: boxTop + (isLand ? 44 : 52),
        width: boxW, display: "flex", flexDirection: "column", gap: 4,
      }}>
        {titleLines.map((raw, i) => {
          const l = raw.replace(/^\*/, "");
          const isAccent = i === titleLines.length - 1 && titleLines.length > 1;
          const fontSize = isAccent
            ? Math.max(isLand ? 48 : 56, Math.min(isLand ? 86 : 100, boxW / (Math.max(l.length, 4) * 0.55)))
            : Math.max(isLand ? 64 : 72, Math.min(isLand ? 112 : 134, boxW / (Math.max(l.length, 4) * 0.62)));
          return (
            <div key={i} style={{overflow: "hidden", padding: "0.08em 0"}}>
              <div style={{
                transform: `translateY(${interpolate(f, [i * 5, i * 5 + 16], [105, 0], {...clamp, easing: Easing.out(Easing.cubic)})}%)`,
                fontFamily: isAccent ? F_SERIF : F_DISPLAY,
                fontStyle: isAccent ? "italic" : "normal",
                fontWeight: 800,
                fontSize,
                lineHeight: 1.1,
                color: isAccent ? C.orange : C.cream,
                letterSpacing: isAccent ? 0 : 1,
                WebkitTextStroke: isAccent ? "0px transparent" : `6px ${C.black}`,
                paintOrder: "stroke fill",
                textShadow: isAccent ? `0 0 40px rgba(var(--a),0.5)` : "0 4px 24px rgba(0,0,0,0.9)",
              } as any}>{l}</div>
            </div>
          );
        })}
      </div>

      {/* Bottom framing rule */}
      <div style={{
        position: "absolute", left: boxX,
        top: boxTop + (isLand ? 44 : 52) + titleLines.length * (isLand ? 108 : 128) + 16,
        height: 2, width: ruleW,
        background: `linear-gradient(90deg, ${ruleColor}, rgba(255,255,255,0.5), transparent)`,
      }} />

      {/* Sub label */}
      {sub ? (
        <Reveal delay={titleLines.length * 5 + 10} style={{
          position: "absolute", left: boxX + 8,
          top: boxTop + (isLand ? 44 : 52) + titleLines.length * (isLand ? 108 : 128) + 26,
        }}>
          <div style={{
            fontFamily: F_COND, fontWeight: 700, fontSize: isLand ? 18 : 22,
            letterSpacing: 4, color: C.cream, opacity: 0.82,
            textShadow: "0 1px 6px rgba(0,0,0,0.85)",
          }}>{sub}</div>
        </Reveal>
      ) : null}
    </AbsoluteFill>
  );
};

// 2. HeadlineWall — staggered news/fact headline strips in upper safe zone
const HeadlineWall: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const Z = ZONE(isLand);

  const headlines: string[] = (o.headlines || o.lines || [up(o.text || o.label || "")]).slice(0, 3).map(up);
  const fadeOut = interpolate(f, [dur - 8, dur], [1, 0], clamp);

  const stripW = isLand ? 1360 : 900;
  const stripX = isLand ? 80 : (width - stripW) / 2;
  const STRIP_H = isLand ? 60 : 72;
  const GAP = isLand ? 10 : 12;
  const startY = Z.top + (isLand ? 10 : 20);

  return (
    <AbsoluteFill style={{opacity: fadeOut, fontFamily: F_DISPLAY}}>
      {headlines.map((hl, i) => {
        const delay = i * 7;
        const sp = Math.min(1, Math.max(0, spring({frame: f - delay, fps, config: {damping: 16, stiffness: 200, mass: 0.65}})));
        const tx = (1 - sp) * -60;
        const alpha = Math.min(1, sp * 1.5);
        const fy = startY + i * (STRIP_H + GAP);
        const fontSize = Math.max(isLand ? 22 : 26, Math.min(isLand ? 36 : 42, stripW / (Math.max(hl.length, 6) * 0.72)));
        return (
          <div key={i} style={{
            position: "absolute", left: stripX, top: fy, width: stripW, height: STRIP_H,
            transform: `translateX(${tx}px)`, opacity: alpha,
            display: "flex", alignItems: "center",
            borderLeft: `4px solid ${C.orange}`,
            boxShadow: `0 2px 16px rgba(0,0,0,0.55), 0 1px 4px rgba(0,0,0,0.7)`,
            background: "rgba(0,0,0,0.62)",
            backdropFilter: "blur(0px)",
            boxSizing: "border-box", paddingLeft: 20, paddingRight: 16,
          }}>
            <div style={{
              fontFamily: F_COND, fontWeight: 700, fontSize,
              letterSpacing: 2, color: C.cream, lineHeight: 1.15,
              textShadow: "0 1px 4px rgba(0,0,0,0.9), 0 4px 16px rgba(0,0,0,0.6)",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              flex: 1,
            }}>{hl}</div>
            {/* thin right accent border */}
            <div style={{
              width: 3, height: "60%", borderRadius: 2,
              background: `linear-gradient(180deg, transparent, ${C.orange}, transparent)`,
              marginLeft: 12, flexShrink: 0,
            }} />
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

// 3. ChartGrow — animated SVG line/trend chart (strokeDashoffset draw-on)
const ChartGrow: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const Z = ZONE(isLand);

  const fadeOut = interpolate(f, [dur - 8, dur], [1, 0], clamp);
  // draw progress: 0 -> 1 over first ~20 frames
  const drawP = interpolate(f, [4, 28], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const dotP = interpolate(f, [24, 36], [0, 1], {...clamp, easing: Easing.out(Easing.back(2))});

  const chartW = isLand ? 1100 : 820;
  const chartH = isLand ? 320 : 380;
  const chartX = (width - chartW) / 2;
  const chartY = isLand ? Z.top + 60 : Z.top + 100;

  // Parse data points from o.data or generate a simple upward trend
  const rawPts: number[] = Array.isArray(o.data) && o.data.length >= 2
    ? o.data.slice(0, 8).map(Number)
    : [20, 35, 28, 50, 44, 68, 60, 85];
  const minV = Math.min(...rawPts);
  const maxV = Math.max(...rawPts);
  const range = Math.max(maxV - minV, 1);
  const n = rawPts.length;
  const pts = rawPts.map((v, i) => ({
    x: chartX + (i / (n - 1)) * chartW,
    y: chartY + chartH - ((v - minV) / range) * chartH * 0.82 - chartH * 0.09,
  }));

  // Build SVG path
  const pathD = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
  // Approximate path length
  let pathLen = 0;
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i].x - pts[i-1].x, dy = pts[i].y - pts[i-1].y;
    pathLen += Math.sqrt(dx*dx + dy*dy);
  }
  const dashOffset = pathLen * (1 - drawP);

  const title = up(o.title || o.label || "TREN DATA");
  const valLabel = up(o.value || "");
  const endPt = pts[pts.length - 1];

  // subtle grid lines
  const gridLines = [0.25, 0.5, 0.75].map(frac => chartY + chartH - frac * chartH * 0.82 - chartH * 0.09);

  return (
    <AbsoluteFill style={{opacity: fadeOut}}>
      {/* bg vignette */}
      <div style={{
        position: "absolute", left: 0, right: 0, top: 0, bottom: 0,
        background: "radial-gradient(ellipse at 50% 42%, rgba(0,0,0,0.3) 0%, rgba(0,0,0,0.55) 100%)",
      }} />

      {/* Chart SVG */}
      <svg style={{position: "absolute", left: 0, top: 0, width: "100%", height: "100%"}} viewBox={`0 0 ${width} ${height}`}>
        {/* grid lines */}
        {gridLines.map((gy, i) => (
          <line key={i} x1={chartX} y1={gy} x2={chartX + chartW} y2={gy}
            stroke={`rgba(255,255,255,0.1)`} strokeWidth={1} strokeDasharray="6 8" />
        ))}
        {/* bottom axis */}
        <line x1={chartX} y1={chartY + chartH} x2={chartX + chartW} y2={chartY + chartH}
          stroke={`rgba(255,255,255,0.22)`} strokeWidth={1.5} />
        {/* trend line */}
        <path d={pathD} fill="none"
          stroke={C.orange} strokeWidth={isLand ? 4 : 5} strokeLinecap="round" strokeLinejoin="round"
          strokeDasharray={pathLen} strokeDashoffset={dashOffset}
          style={{filter: `drop-shadow(0 0 8px rgba(var(--a),0.7))`}}
        />
        {/* glow duplicate */}
        <path d={pathD} fill="none"
          stroke={`rgba(var(--a),0.25)`} strokeWidth={isLand ? 14 : 18} strokeLinecap="round" strokeLinejoin="round"
          strokeDasharray={pathLen} strokeDashoffset={dashOffset}
        />
        {/* endpoint glowing dot */}
        <circle cx={endPt.x} cy={endPt.y} r={(isLand ? 10 : 13) * dotP}
          fill={C.orange} opacity={dotP}
          style={{filter: `drop-shadow(0 0 12px ${C.orange})`}} />
        <circle cx={endPt.x} cy={endPt.y} r={(isLand ? 22 : 28) * dotP}
          fill="none" stroke={C.orange} strokeWidth={2} opacity={dotP * 0.5} />
      </svg>

      {/* Title */}
      <Reveal delay={2} style={{position: "absolute", left: chartX, top: chartY - (isLand ? 48 : 60)}}>
        <div style={{
          fontFamily: F_COND, fontWeight: 700, fontSize: isLand ? 24 : 28,
          letterSpacing: 5, color: C.orange,
          textShadow: "0 1px 6px rgba(0,0,0,0.9)",
        }}>{title}</div>
      </Reveal>

      {/* End-point value label */}
      {valLabel ? (
        <div style={{
          position: "absolute",
          left: Math.min(endPt.x + 16, width - (isLand ? 240 : 200)),
          top: endPt.y - (isLand ? 44 : 52),
          opacity: dotP,
          transform: `scale(${dotP})`,
          transformOrigin: "left center",
        }}>
          <div style={{
            fontFamily: F_DISPLAY, fontWeight: 800, fontSize: isLand ? 32 : 38,
            color: C.cream, textShadow: "0 2px 8px rgba(0,0,0,0.9)",
          }}>{valLabel}</div>
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

// 4. FrameBreak — editorial inset: active clip scales down into framed card
const FrameBreak: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const Z = ZONE(isLand);

  // Scale video down to 0.70 at center over first 10 frames, hold, then scale back up for exit
  const scaleIn = interpolate(f, [0, 12], [1.0, 0.70], {...clamp, easing: Easing.out(Easing.cubic)});
  const scaleOut = interpolate(f, [dur - 10, dur], [0.70, 1.0], {...clamp, easing: Easing.in(Easing.cubic)});
  const scale = f < dur - 10 ? scaleIn : scaleOut;

  // Border fade in
  const borderAlpha = interpolate(f, [6, 18], [0, 1], clamp);
  const fadeOut = interpolate(f, [dur - 8, dur], [1, 0], clamp);

  const label = up(o.label || o.text || "");
  const labelAlpha = interpolate(f, [10, 20], [0, 1], clamp);

  const cardW = width * scale;
  const cardH = height * scale;
  const cardX = (width - cardW) / 2;
  const cardY = (height - cardH) / 2;

  const borderColor = C.orange;
  const hairline = isLand ? 2 : 2.5;

  return (
    <AbsoluteFill style={{opacity: fadeOut}}>
      {/* Dark matte background reveals as clip shrinks */}
      <div style={{
        position: "absolute", left: 0, right: 0, top: 0, bottom: 0,
        background: "#0a0a0a",
        opacity: interpolate(f, [0, 12], [0, 1 - scale * scale], clamp),
      }} />

      {/* The scaled clip area — just a visual frame overlay since actual video is base layer */}
      {/* Hairline border */}
      <svg style={{position: "absolute", left: 0, top: 0, width: "100%", height: "100%"}} viewBox={`0 0 ${width} ${height}`}>
        {/* outer hairline frame */}
        <rect
          x={cardX} y={cardY} width={cardW} height={cardH}
          fill="none"
          stroke={borderColor}
          strokeWidth={hairline}
          opacity={borderAlpha}
          rx={4}
        />
        {/* corner accent ticks — top-left */}
        <line x1={cardX} y1={cardY + 36} x2={cardX} y2={cardY} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
        <line x1={cardX} y1={cardY} x2={cardX + 36} y2={cardY} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
        {/* top-right */}
        <line x1={cardX + cardW - 36} y1={cardY} x2={cardX + cardW} y2={cardY} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
        <line x1={cardX + cardW} y1={cardY} x2={cardX + cardW} y2={cardY + 36} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
        {/* bottom-left */}
        <line x1={cardX} y1={cardY + cardH - 36} x2={cardX} y2={cardY + cardH} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
        <line x1={cardX} y1={cardY + cardH} x2={cardX + 36} y2={cardY + cardH} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
        {/* bottom-right */}
        <line x1={cardX + cardW - 36} y1={cardY + cardH} x2={cardX + cardW} y2={cardY + cardH} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
        <line x1={cardX + cardW} y1={cardY + cardH - 36} x2={cardX + cardW} y2={cardY + cardH} stroke={borderColor} strokeWidth={hairline * 2.5} opacity={borderAlpha} />
      </svg>

      {/* Optional label below frame */}
      {label ? (
        <div style={{
          position: "absolute",
          left: cardX,
          top: cardY + cardH + (isLand ? 14 : 18),
          width: cardW,
          textAlign: "center",
          opacity: labelAlpha,
        }}>
          <div style={{
            fontFamily: F_COND, fontWeight: 700, fontSize: isLand ? 20 : 24,
            letterSpacing: 5, color: C.cream, opacity: 0.85,
            textShadow: "0 1px 6px rgba(0,0,0,0.9)",
          }}>{label}</div>
        </div>
      ) : null}
    </AbsoluteFill>
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
  const boxH = isLand ? 560 : 700;
  const boxTop = Math.round(zoneMid(isLand) - boxH / 2);
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
                fontWeight: accent ? 700 : 800, fontSize: size, lineHeight: 1.15,
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
  const badge = up(o.badge || "CATATAN BUDAYA");
  const lines = wrapLines([up(o.text)], isLand ? 42 : 22).slice(0, 2).map((l) => l.replace(/^\*/, ""));
  const size = fitSize(lines.reduce((a, b) => (a.length >= b.length ? a : b), ""), isLand ? 38 : 44, isLand ? 1100 : 820);
  const lh = Math.round(size * 1.25);
  const gh = 22 + 28 + 8 + lines.length * lh + 26;
  const gx = isLand ? 80 : 60;
  const gw = isLand ? 1240 : 960;
  const gy = ZONE(isLand).bottom - gh;
  return (
    <Scene dur={dur} moving={false}>
      <Panel x={gx} y={gy} w={gw} h={gh} style={{transform: `translateX(${(1 - s) * -80}px)`, opacity: Math.min(1, s * 1.6)}}>
        <div style={{position: "relative", padding: "22px 34px 26px 46px"}}>
          <div style={{
            position: "absolute", left: 22, top: 22, bottom: 26, width: 6, borderRadius: 3,
            background: `linear-gradient(180deg, ${C.orange}, rgba(var(--a),.35))`,
            boxShadow: `0 0 18px rgba(var(--a),.45)`,
          }} />
          <Reveal delay={2}>
            <div style={{fontFamily: F_COND, fontWeight: 700, fontSize: 23, lineHeight: "28px", letterSpacing: 4, color: C.orange, textShadow: "0 2px 8px rgba(0,0,0,0.8), 0 6px 24px rgba(0,0,0,0.55)"}}>{badge}</div>
          </Reveal>
          <div style={{height: 8}} />
          {lines.map((l, i) => (
            <Reveal key={i} delay={6 + i * 4}>
              <div style={{fontWeight: 800, fontSize: size, color: C.cream, lineHeight: `${lh}px`, textShadow: "0 2px 6px rgba(0,0,0,0.85), 0 8px 30px rgba(0,0,0,0.65), 0 18px 48px rgba(0,0,0,0.45)"}}>
                <MarkedText text={l} delay={14 + i * 4} color={C.orange} ink={C.black} />
              </div>
            </Reveal>
          ))}
        </div>
        <div style={{position: "absolute", left: 22, bottom: 0, height: 4, width: `${prog}%`, maxWidth: gw - 44, background: `linear-gradient(90deg, ${C.orange}, ${C.cream})`, boxShadow: "0 2px 10px rgba(0,0,0,0.5)"}} />
      </Panel>
    </Scene>
  );
};

const Stat: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const Z = ZONE(isLand);
  const valueRaw = up(o.value || o.text || (Array.isArray(o.lines) ? o.lines[0] : "") || "");
  const labelRaw = up(o.label || "");
  const vLines = wrapLines([valueRaw], isLand ? 22 : 14).slice(0, 3).map((l) => l.replace(/^\*/, ""));
  const lLines = labelRaw ? wrapLines([labelRaw], isLand ? 40 : 24).slice(0, 2).map((l) => l.replace(/^\*/, "")) : [];
  const maxW = isLand ? 1400 : 860;
  const longest = Math.max(...vLines.map((l) => l.length), 3);
  const vSize = Math.round(Math.max(54, Math.min(isLand ? 150 : 168, maxW / (longest * 0.72))));
  const vLh = Math.round(vSize * 1.18);
  const lSize = isLand ? 32 : 34;
  const lLh = Math.round(lSize * 1.4);
  const bgOp = interpolate(f, [0, 10], [0, 1], clamp);
  const bar = Math.min(1, Math.max(0, spring({frame: f - vLines.length * 4 - 6, fps, config: SPR})));
  const barMax = Math.min(isLand ? 560 : 460, Math.max(180, longest * vSize * 0.4));
  return (
    <Scene dur={dur} moving={false}>
      <div style={{
        position: "absolute", left: 40, right: 40, top: Z.top, height: Z.bottom - Z.top, opacity: bgOp,
        background: "radial-gradient(ellipse at center, rgba(8,12,10,0.5) 0%, rgba(8,12,10,0) 70%)",
      }} />
      <div style={{
        position: "absolute", left: 60, right: 60, top: Z.top, height: Z.bottom - Z.top,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      }}>
        {vLines.map((l, i) => (
          <Reveal key={i} delay={i * 4} style={{textAlign: "center", maxWidth: width - 140}}>
            <div style={{
              fontFamily: F_SERIF, fontStyle: "italic", fontWeight: 700, fontSize: vSize, lineHeight: `${vLh}px`,
              color: C.cream, textAlign: "center", padding: "6px 0",
              textShadow: "0 2px 4px rgba(0,0,0,0.45), 0 8px 32px rgba(0,0,0,0.5)",
            } as any}>{l}</div>
          </Reveal>
        ))}
        <div style={{
          marginTop: 22, height: 4, borderRadius: 2,
          background: `linear-gradient(90deg, transparent, ${C.orange}, transparent)`,
          width: barMax * bar,
        }} />
        {lLines.length ? (
          <div style={{marginTop: 22, textAlign: "center", maxWidth: width - 180}}>
            {lLines.map((l, i) => (
              <Reveal key={i} delay={vLines.length * 4 + 6 + i * 4} style={{textAlign: "center"}}>
                <div style={{
                  fontFamily: F_COND, fontWeight: 700, fontSize: lSize, lineHeight: `${lLh}px`, letterSpacing: 3,
                  color: C.cream, opacity: 0.94, textAlign: "center",
                  textShadow: "0 1px 3px rgba(0,0,0,0.6), 0 4px 18px rgba(0,0,0,0.4)",
                } as any}>{l}</div>
              </Reveal>
            ))}
          </div>
        ) : null}
      </div>
    </Scene>
  );
};

const List: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const items: any[] = (o.items || []).slice(0, 4);
  const n = Math.max(items.length, 1);
  const ROW = 54, GAP = 20, HEAD = 58;
  const h = 24 + HEAD + n * ROW + (n - 1) * GAP + 24;
  const top = Math.round(zoneMid(isLand) - h / 2);
  const gx = isLand ? 310 : 60;
  const gw = isLand ? 1300 : 960;
  const p = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR})));
  const textShadow = "0 2px 6px rgba(0,0,0,0.85), 0 8px 28px rgba(0,0,0,0.6)";
  return (
    <>
      <Dim dur={dur} />
      <Scene dur={dur} moving={false}>
        <Panel x={gx} y={top} w={gw} h={h} style={{transform: `translateY(${(1 - p) * 60}px)`, opacity: Math.min(1, p * 1.5)}}>
          <div style={{position: "absolute", left: 30, top: 24, bottom: 24, right: 34}}>
            <div style={{
              position: "absolute", left: 0, top: 2, height: 30, width: 6, borderRadius: 3,
              background: `linear-gradient(180deg, ${C.orange}, rgba(var(--a),.35))`,
              boxShadow: "0 0 16px rgba(var(--a),.5)",
            }} />
            <div style={{paddingLeft: 22, height: 44, boxSizing: "border-box", paddingBottom: 12, borderBottom: "1px solid rgba(255,255,255,0.22)"}}>
              <Reveal delay={2}>
                <div style={{fontFamily: F_COND, fontWeight: 700, fontSize: 24, letterSpacing: 4, color: C.orange, lineHeight: "30px", whiteSpace: "nowrap", textShadow}}>
                  {up(o.title || "RINGKASAN FAKTA")}
                </div>
              </Reveal>
            </div>
            <div style={{position: "absolute", left: 0, right: 0, top: HEAD, display: "flex", flexDirection: "column", gap: GAP}}>
              {items.map((it, i) => {
                const st = Math.round((it._t || i * 0.45) * fps);
                const q = Math.min(1, Math.max(0, spring({frame: f - st, fps, config: SPR})));
                const txt = up(it.text);
                return (
                  <div key={i} style={{display: "flex", alignItems: "center", gap: 20, height: ROW}}>
                    <div style={{
                      minWidth: 42, height: 42, borderRadius: 12, background: C.orange, color: C.black,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontFamily: F_DISPLAY, fontWeight: 800, fontSize: 22,
                      transform: `scale(${q})`, boxShadow: "0 4px 14px rgba(var(--a),.45), 0 2px 8px rgba(0,0,0,0.5)",
                    }}>{i + 1}</div>
                    <Reveal delay={st} style={{flex: 1}}>
                      <div style={{fontWeight: 800, fontSize: fitSize(txt, isLand ? 34 : 38, isLand ? 1080 : 760), color: C.cream, lineHeight: 1.25, whiteSpace: "nowrap", textShadow}}>{txt}</div>
                    </Reveal>
                  </div>
                );
              })}
            </div>
          </div>
        </Panel>
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
  const boxH = isLand ? 500 : 540;
  const boxTop = Math.round(zoneMid(isLand) - boxH / 2);
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
          position: "absolute", left: isLand ? 240 : 70, right: isLand ? 240 : 70, top: boxTop, height: boxH, borderRadius: 30,
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
  const iy = Math.round(zoneMid(isLand) - (isLand ? 520 : 590) / 2);
  const cy = iy + (isLand ? 200 : 225);
  return (
    <Scene dur={dur} moving={false}>
      <svg width={width} height={height} style={{position: "absolute", left: 0, top: 0}}>
        <g transform={`translate(${cx} ${cy + floatY}) scale(${Math.max(0, pop)})`} opacity={pIn}>
          <IconArt name={o.icon} draw={draw} f={f} />
        </g>
        <circle cx={cx} cy={cy} r={95 + ring * 185} fill="none" stroke={C.orange}
          strokeWidth={6 * (1 - ring) + 1} opacity={(1 - ring) * (f >= 6 ? 1 : 0)} />
      </svg>
      <div style={{position: "absolute", left: isLand ? 490 : 110, right: isLand ? 490 : 110, top: iy + (isLand ? 390 : 426), textAlign: "center"}}>
        <Reveal delay={10}>
          <div style={{fontWeight: 800, fontSize: fitSize(label, 40, isLand ? 920 : 820), color: C.cream, textShadow: "0 2px 6px rgba(0,0,0,0.85), 0 8px 30px rgba(0,0,0,0.65), 0 18px 48px rgba(0,0,0,0.45)"}}>{label}</div>
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
  const qTop = Math.round(zoneMid(isLand) - (isLand ? 230 : 290));
  return (
    <Scene dur={dur}>
      <div style={{
        position: "absolute", left: 0, right: 0, top: qTop, textAlign: "center",
        fontFamily: F_SERIF, fontStyle: "italic", fontWeight: 700,
        fontSize: isLand ? 310 : 380, color: C.orange, lineHeight: 1,
        transform: `scale(${Math.max(0, s)}) rotate(${wob}deg)`,
        WebkitTextStroke: `8px ${C.black}`, paintOrder: "stroke fill",
        textShadow: `0 0 ${glow}px rgba(var(--a),.7), 0 10px 40px rgba(0,0,0,0.55)`,
      } as any}>?</div>
      <div style={{position: "absolute", left: isLand ? 360 : 90, top: qTop + (isLand ? 380 : 470), width: isLand ? 1200 : 900, opacity: pill, transform: `translateY(${(1 - pill) * 40}px)`, textAlign: "center"}}>
        <Reveal delay={8}>
          <div style={{fontWeight: 800, fontSize: fitSize(label, 40, isLand ? 1100 : 800), color: C.cream, lineHeight: 1.25, textShadow: "0 2px 6px rgba(0,0,0,0.85), 0 8px 30px rgba(0,0,0,0.65), 0 18px 48px rgba(0,0,0,0.45)"}}>{label}</div>
        </Reveal>
      </div>
    </Scene>
  );
};

const MapToText: React.FC<any> = ({o, dur}) => {
  const {width, height} = useVideoConfig();
  const isLand = width > height;
  const t = clipWords(up(o.label || o.sub || o.text || ""), isLand ? 76 : 40);
  if (!t) return null;
  const nWords = t.split(/\s+/).filter(Boolean).length;
  if (nWords <= 3 && t.length <= 24) return <Big o={{...o, lines: [t]}} dur={dur} />;
  return <Lower o={{...o, text: t, badge: undefined}} dur={dur} />;
};

const Caps: React.FC<any> = ({cue, dur, fps}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const isLand = width > height;
  // Whole phrase fades in quickly as a unit (no word-by-word delay)
  const inn = interpolate(f, [0, 5], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const out = interpolate(f, [dur - 4, dur], [1, 0], clamp);
  const longest = Math.max(...cue.lines.map((ln: any[]) => ln.map((x: any) => x.w).join(" ").length), 1);
  const fs = Math.max(isLand ? 28 : 32, Math.min(isLand ? 40 : 50, (isLand ? 1400 : 840) / (longest * 0.66)));
  const lh = Math.round(fs * 1.3);
  return (
    <AbsoluteFill style={{fontFamily: F_DISPLAY, opacity: inn * out}}>
      <div style={{
        position: "absolute", left: 0, right: 0, bottom: 0, height: isLand ? 300 : 420,
        background: "linear-gradient(180deg, rgba(0,0,0,0) 0%, rgba(0,0,0,0.18) 30%, rgba(0,0,0,0.42) 72%, rgba(0,0,0,0.56) 100%)",
        pointerEvents: "none",
      }} />
      <div style={{position: "absolute", left: "8%", width: "84%", bottom: height - ZONE(isLand).sub, display: "flex", justifyContent: "center"}}>
        <div style={{
          maxWidth: "100%", boxSizing: "border-box",
          display: "flex", flexDirection: "column", alignItems: "center", gap: 2, textAlign: "center",
        }}>
          {cue.lines.map((ln: any[], li: number) => (
            <div key={li} style={{display: "flex", justifyContent: "center", gap: fs * 0.28, flexWrap: "nowrap", whiteSpace: "nowrap"}}>
              {ln.map((wd: any, wi: number) => {
                // Determine if this word is currently the active spoken word
                const wordStart = Math.round((wd.t0 - cue.s) * fps);
                const wordEnd = Math.round((wd.t1 - cue.s) * fps);
                const isActive = f >= wordStart && f < wordEnd + 4;
                // All words visible from frame 0, full white; active word gets gold highlight
                return (
                  <div key={wi} style={{
                    fontWeight: 700, fontSize: fs, lineHeight: `${lh}px`,
                    color: (wd.kw || isActive) ? C.orange : "#FFFFFF",
                    textShadow: SUB_SHADOW,
                    transition: "color 0.05s",
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
  big: Big, lower: Lower, stat: Stat, list: List, quote: List, solid: Solid,
  icon: Icon, qmark: QMark, map: MapToText,
  cover_story: CoverStory, headline_wall: HeadlineWall, chart_grow: ChartGrow, frame_break: FrameBreak,
};

export const Overlay: React.FC<any> = ({overlays = [], fps = 30, images = [], captions = [], theme = null}) => {
  const C = useMemo(() => ({...DEFAULT_C, ...(theme && theme.hex ? theme.hex : {})}), [theme]);
  const vars: any = theme && theme.vars ? theme.vars : {};
  const cfg = useMemo(() => ({}), []);
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
    """Dipertahankan hanya agar impor lama tidak error. Peta sudah dihapus."""
    return None
