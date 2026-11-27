import os

IS_LONG = os.environ.get("VIDEO_MODE", "").strip().upper() == "LONG"
OUT_W = 1920 if IS_LONG else 1080
OUT_H = 1080 if IS_LONG else 1920

REMO_FILES = {
    "package.json": r"""{"name":"overlay","version":"1.0.0","private":true,"dependencies":{"remotion":"4.0.352","@remotion/cli":"4.0.352","react":"18.3.1","react-dom":"18.3.1","@remotion/google-fonts":"4.0.352","@remotion/motion-blur":"4.0.352","@remotion/paths":"4.0.352","@remotion/transitions":"4.0.352"}}""",
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
import {CameraMotionBlur} from "@remotion/motion-blur";
import {evolvePath} from "@remotion/paths";
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
const CfgCtx = createContext<any>({glass: null});

const clamp: any = {extrapolateLeft: "clamp", extrapolateRight: "clamp"};
const SPR_SNAP = {damping: 15, stiffness: 260, mass: 0.65};
const SPR_FLOAT = {damping: 14, stiffness: 140, mass: 0.85};
const up = (s: any) => String(s || "").toUpperCase();

// ZONA AMAN MUTLAK: animasi dikunci di top..bottom, subtitle Whisper dikunci di `sub`.
const ZONE = (isLand: boolean) => ({
  top: isLand ? 90 : 280,
  bottom: isLand ? 810 : 1140,
  sub: isLand ? 996 : 1470,
});
const zoneMid = (isLand: boolean) => (ZONE(isLand).top + ZONE(isLand).bottom) / 2;

const SUB_SHADOW = "0 1px 2px rgba(0,0,0,0.65), 0 3px 12px rgba(0,0,0,0.45)";

const fitSize = (txt: string, max: number, width: number) =>
  Math.max(26, Math.min(max, width / (Math.max(txt.length, 1) * 0.74)));

const keyWord = (text: string): string => {
  const ws = String(text || "").split(/\s+/).filter(Boolean);
  let best = "";
  for (const w of ws) {
    if (w.replace(/[^A-Za-z0-9\u00c0-\u00ff]/g, "").length >= best.replace(/[^A-Za-z0-9\u00c0-\u00ff]/g, "").length) {
      best = w;
    }
  }
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
  return out.slice(0, 3);
};

// Pembungkus transisi keluar organik (scale + blur + drift halus)
const MotionStage: React.FC<any> = ({dur, children, driftDir = -1}) => {
  const f = useCurrentFrame();
  const outP = interpolate(f, [dur - 8, dur], [0, 1], {...clamp, easing: Easing.in(Easing.cubic)});
  const opacity = 1 - outP;
  const scale = 1 - outP * 0.05;
  const ty = outP * 26 * driftDir;
  return (
    <AbsoluteFill style={{
      opacity,
      transform: `translateY(${ty}px) scale(${scale})`,
      fontFamily: F_DISPLAY,
    }}>
      {children}
    </AbsoluteFill>
  );
};

// Reveal dengan mask vertikal + kemiringan dinamis saat masuk
const WipeReveal: React.FC<any> = ({delay = 0, children, style = {}, angle = 2}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = Math.min(1, Math.max(0, spring({frame: f - delay, fps, config: SPR_SNAP})));
  const skew = (1 - p) * angle;
  return (
    <div style={{overflow: "hidden", padding: "0.14em 0", margin: "-0.14em 0", ...style}}>
      <div style={{
        transform: `translateY(${(1 - p) * 108}%) skewY(${skew}deg)`,
        opacity: Math.min(1, p * 1.8),
      }}>
        {children}
      </div>
    </div>
  );
};

// Stabilo organik menyapu kata kunci
const BrushMark: React.FC<any> = ({delay = 0, color, children}) => {
  const f = useCurrentFrame();
  const w = interpolate(f, [delay, delay + 11], [0, 1], {...clamp, easing: Easing.out(Easing.exp)});
  return (
    <span style={{position: "relative", display: "inline-block", padding: "0 0.24em"}}>
      <span style={{
        position: "absolute", left: -2, right: -2, top: "8%", bottom: "4%",
        background: color, borderRadius: 6,
        transformOrigin: "0 50%", transform: `scaleX(${w}) skewX(-8deg)`,
        boxShadow: "0 4px 18px rgba(0,0,0,0.35)",
      }} />
      <span style={{position: "relative"}}>{children}</span>
    </span>
  );
};

const HighlightedSentence: React.FC<any> = ({text, delay = 6, color, ink}) => {
  const key = keyWord(text);
  const idx = key ? text.lastIndexOf(key) : -1;
  if (idx < 0 || text.trim().split(/\s+/).length < 2) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <BrushMark delay={delay} color={color}>
        <span style={{color: ink, WebkitTextStroke: "0px transparent"} as any}>{key}</span>
      </BrushMark>
      {text.slice(idx + key.length)}
    </>
  );
};

// ----------------------------------------------------------------------------
// 1. KINETIC_TYPO: Tipografi berlapis dengan parallax & garis pemindai hidup
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// 0. ROTO_TYPO: Tipografi raksasa di BELAKANG subjek (layered rotoscoping)
// ----------------------------------------------------------------------------

const CameraRig: React.FC<{children: React.ReactNode}> = ({children}) => {
  const f = useCurrentFrame();
  const panX = Math.sin(f * 0.035) * 6;
  const panY = Math.cos(f * 0.028) * 5;
  const rot = Math.sin(f * 0.022) * 0.35;
  const zoom = 1.01 + Math.sin(f * 0.03) * 0.008;
  return (
    <div style={{
      position: "absolute",
      inset: 0,
      transform: `translate3d(${panX}px, ${panY}px, 0) rotate(${rot}deg) scale(${zoom})`,
      transformOrigin: "center center",
    }}>
      {children}
    </div>
  );
};


const PathCallout: React.FC<{d?: string; color?: string; startFrame?: number}> = ({
  d = "M 40 220 Q 180 60 360 140 T 620 90",
  color = "#FF4D2D",
  startFrame = 0,
}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const prog = Math.min(1, Math.max(0, spring({frame: f - startFrame, fps, config: SPR_SNAP})));
  const evo = evolvePath(prog, d);
  return (
    <svg width="700" height="300" viewBox="0 0 700 300" style={{overflow: "visible"}}>
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={10}
        strokeLinecap="round"
        strokeDasharray={evo.strokeDasharray}
        strokeDashoffset={evo.strokeDashoffset}
      />
    </svg>
  );
};


// ----------------------------------------------------------------------------
// STAT_COUNTER: Angka berjalan dinamis (odometer / count-up dengan spring)
// ----------------------------------------------------------------------------
const StatCounter: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const p = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR_FLOAT})));

  const rawNum = Number(o.value ?? String(o.number || "0").replace(/[^0-9.-]/g, "")) || 0;
  const curVal = Math.round(rawNum * p);
  const formatted = curVal.toLocaleString("id-ID");
  const prefix = o.prefix || "";
  const suffix = o.suffix || "";
  const label = (o.label || o.text || "").toUpperCase();

  const boxH = isLand ? 420 : 540;
  const boxTop = Math.round(zoneMid(isLand) - boxH / 2);

  return (
    <MotionStage dur={dur}>
      <div style={{
        position: "absolute", left: 48, right: 48, top: boxTop, height: boxH,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        transform: `translateY(${(1 - p) * 45}px) scale(${0.92 + p * 0.08})`,
        opacity: p,
      }}>
        {o.kicker ? (
          <div style={{
            fontFamily: F_BODY, fontWeight: 700, fontSize: isLand ? 24 : 28,
            letterSpacing: 3, color: C.cream, backgroundColor: C.orange,
            padding: "6px 18px", borderRadius: 6, marginBottom: 14,
            textTransform: "uppercase",
          }}>
            {o.kicker}
          </div>
        ) : null}
        <div style={{
          fontFamily: F_COND, fontWeight: 700,
          fontSize: isLand ? 132 : 148, lineHeight: 1, letterSpacing: 2,
          color: C.orange,
          WebkitTextStroke: `6px ${C.black}`,
          paintOrder: "stroke fill",
          textShadow: "0 14px 36px rgba(0,0,0,0.9)",
          textAlign: "center",
        }}>
          {prefix}{formatted}{suffix}
        </div>
        {label ? (
          <div style={{
            fontFamily: F_BODY, fontWeight: 800,
            fontSize: isLand ? 34 : 40, lineHeight: 1.2,
            color: C.cream, marginTop: 14, textAlign: "center",
            textShadow: "0 4px 16px rgba(0,0,0,0.95)",
          }}>
            {label}
          </div>
        ) : null}
      </div>
    </MotionStage>
  );
};


// ----------------------------------------------------------------------------
// DRAW_ON: Coretan lingkaran, garis bawah, & panah vektor terlukis otomatis
// ----------------------------------------------------------------------------
const DrawOn: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const p = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR_FLOAT})));

  const shape = o.shape || "circle"; // "circle" | "underline" | "arrow"
  const label = (o.label || o.text || "").toUpperCase();
  const paths: Record<string, string> = {
    circle: "M 350 40 C 560 35, 650 110, 640 190 C 630 270, 480 310, 330 305 C 160 300, 60 245, 70 165 C 80 85, 220 35, 385 45",
    underline: "M 70 210 Q 350 185 630 205 M 110 235 Q 360 215 590 232",
    arrow: "M 110 250 Q 260 110 540 125 M 485 85 L 550 126 L 490 172",
  };
  const d = paths[shape] || paths.circle;
  const evo = evolvePath(p, d);

  const boxH = isLand ? 420 : 520;
  const boxTop = Math.round(zoneMid(isLand) - boxH / 2);

  return (
    <MotionStage dur={dur}>
      <div style={{
        position: "absolute", left: 40, right: 40, top: boxTop, height: boxH,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      }}>
        <svg width="700" height="340" viewBox="0 0 700 340" style={{overflow: "visible"}}>
          <path
            d={d}
            fill="none"
            stroke={C.orange}
            strokeWidth={12}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={evo.strokeDasharray}
            strokeDashoffset={evo.strokeDashoffset}
            style={{filter: "drop-shadow(0 8px 18px rgba(0,0,0,0.85))"}}
          />
        </svg>
        {label ? (
          <div style={{
            position: "absolute",
            fontFamily: F_COND, fontWeight: 700,
            fontSize: isLand ? 76 : 84, lineHeight: 1.05, letterSpacing: 1.5,
            color: C.cream,
            WebkitTextStroke: `5px ${C.black}`,
            paintOrder: "stroke fill",
            textShadow: "0 10px 28px rgba(0,0,0,0.9)",
            textAlign: "center",
            opacity: p,
            transform: `scale(${0.9 + p * 0.1})`,
          }}>
            {label}
          </div>
        ) : null}
      </div>
    </MotionStage>
  );
};

const RotoTypo: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const rawLines = Array.isArray(o.lines) && o.lines.length ? o.lines : [o.text || "SOROTAN"];
  const lines = wrapLines(rawLines, isLand ? 18 : 11);
  const bgDim = interpolate(f, [0, 8], [0, 1], clamp);
  const pulse = 1 + Math.sin(f * 0.08) * 0.015;
  const boxH = isLand ? 560 : 760;
  const boxTop = Math.round(zoneMid(isLand) - boxH / 2);

  return (
    <MotionStage dur={dur}>
      {/* Lapisan 1: Redup lokal + pola titik halftone halus */}
      <div style={{
        position: "absolute", inset: 0, opacity: bgDim * 0.65,
        backgroundImage: "radial-gradient(rgba(255,255,255,0.14) 1.5px, transparent 1.5px)",
        backgroundSize: "26px 26px",
      }} />
      {/* Lapisan 2: Tipografi raksasa (berada di belakang subjek) */}
      <div style={{
        position: "absolute", left: 40, right: 40, top: boxTop, height: boxH,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        transform: `scale(${pulse})`,
      }}>
        {lines.map((raw, i) => {
          const l = raw.replace(/^\*/, "");
          const isAccent = raw.startsWith("*") || i === lines.length - 1;
          const p = Math.min(1, Math.max(0, spring({frame: f - i * 4, fps, config: SPR_SNAP})));
          return (
            <div key={i} style={{
              fontFamily: F_COND, fontWeight: 700,
              fontSize: isLand ? 156 : 168, lineHeight: 0.96, letterSpacing: 2,
              color: isAccent ? C.orange : C.cream,
              WebkitTextStroke: `6px ${C.black}`, paintOrder: "stroke fill", textShadow: "0 12px 40px rgba(0,0,0,0.9)",
              transform: `translateY(${(1 - p) * 70}px) scale(${0.92 + p * 0.08})`,
              opacity: p,
              textAlign: "center",
            }}>
              {l}
            </div>
          );
        })}
      </div>
      {/* Lapisan 3: Potongan subjek transparan (rotoSrc) di depan teks */}
      {o.rotoSrc ? (
        <OffthreadVideo
          src={staticFile(o.rotoSrc)}
          style={{position: "absolute", inset: 0, width, height, objectFit: "cover"}}
        />
      ) : null}
    </MotionStage>
  );
};

const KineticTypo: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const rawLines = Array.isArray(o.lines) && o.lines.length
    ? o.lines
    : [o.text || o.value || "SOROTAN UTAMA"];
  const lines = wrapLines(rawLines, isLand ? 26 : 16);
  const kicker = up(o.kicker || o.badge || "");
  const starIdx = lines.findIndex((l) => l.startsWith("*"));
  const accentIdx = starIdx >= 0 ? starIdx : (lines.length > 1 ? lines.length - 1 : -1);

  // Gerakan parallax halus sepanjang durasi tampil
  const driftX = interpolate(f, [0, dur], [-14, 14], clamp);
  const lineGrow = Math.min(1, Math.max(0, spring({frame: f - 2, fps, config: SPR_SNAP})));
  const bgDim = interpolate(f, [0, 8], [0, 1], clamp);

  const boxH = isLand ? 520 : 680;
  const boxTop = Math.round(zoneMid(isLand) - boxH / 2);
  const maxW = isLand ? 1520 : 880;

  return (
    <MotionStage dur={dur}>
      {/* Vignette fokus lokal tanpa kotak */}
      <div style={{
        position: "absolute", left: 0, right: 0, top: boxTop - 40, height: boxH + 80, opacity: bgDim,
        background: "radial-gradient(ellipse at center, rgba(6,10,8,0.68) 0%, rgba(6,10,8,0) 74%)",
      }} />

      <div style={{
        position: "absolute", left: 60, right: 60, top: boxTop, height: boxH,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      }}>
        {kicker ? (
          <WipeReveal delay={0} style={{marginBottom: 14}}>
            <div style={{
              fontFamily: F_COND, fontWeight: 700, fontSize: isLand ? 26 : 28,
              letterSpacing: 6, color: C.orange,
              transform: `translateX(${-driftX * 0.6}px)`,
              textShadow: "0 2px 12px rgba(0,0,0,0.8)",
            }}>
              {kicker}
            </div>
          </WipeReveal>
        ) : null}

        {lines.map((raw, i) => {
          const l = raw.replace(/^\*/, "");
          const accent = i === accentIdx;
          const size = accent
            ? Math.min(isLand ? 96 : 108, Math.max(46, maxW / (Math.max(l.length, 4) * 0.58)))
            : Math.min(isLand ? 102 : 114, Math.max(46, maxW / (Math.max(l.length, 4) * 0.76)));
          const paraDir = i % 2 === 0 ? 1 : -1;
          return (
            <WipeReveal key={i} delay={3 + i * 4} angle={paraDir * 2.5} style={{textAlign: "center", maxWidth: width - 120}}>
              <div style={{
                fontFamily: accent ? F_SERIF : F_DISPLAY,
                fontStyle: accent ? "italic" : "normal",
                fontWeight: accent ? 700 : 800,
                fontSize: size,
                lineHeight: 1.14,
                color: accent ? C.black : C.cream,
                letterSpacing: accent ? 0 : 1.5,
                WebkitTextStroke: accent ? "0px transparent" : `8px ${C.black}`,
                paintOrder: "stroke fill",
                textShadow: accent ? "none" : "0 10px 34px rgba(0,0,0,0.9)",
                transform: `translateX(${driftX * paraDir * 0.45}px)`,
                padding: "2px 0",
              } as any}>
                {accent ? (
                  <BrushMark delay={6 + i * 4} color={C.orange}>{l}</BrushMark>
                ) : lines.length === 1 ? (
                  <HighlightedSentence text={l} delay={7} color={C.orange} ink={C.black} />
                ) : l}
              </div>
            </WipeReveal>
          );
        })}

        {/* Garis aksen dinamis di bawah teks */}
        <div style={{
          marginTop: 24, height: 5, borderRadius: 3,
          width: (isLand ? 680 : 480) * lineGrow,
          background: `linear-gradient(90deg, transparent, ${C.orange}, ${C.cream}, transparent)`,
          boxShadow: `0 0 18px ${C.orange}`,
        }} />
      </div>
    </MotionStage>
  );
};

// ----------------------------------------------------------------------------
// 2. MARKER_QUOTE: Kutipan editorial / fakta dengan garis vertikal & stabilo hidup
// ----------------------------------------------------------------------------
const MarkerQuote: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const Z = ZONE(isLand);

  const rawText = up(o.text || (Array.isArray(o.lines) ? o.lines.join(" ") : "") || o.label || "");
  const badge = up(o.badge || o.kicker || "FAKTA LAPANGAN");
  const lines = wrapLines([rawText], isLand ? 34 : 20).map((l) => l.replace(/^\*/, ""));
  const longest = lines.reduce((a, b) => (a.length >= b.length ? a : b), "");
  const fs = fitSize(longest, isLand ? 52 : 56, isLand ? 1320 : 860);
  const lh = Math.round(fs * 1.24);

  const barGrow = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR_SNAP})));
  const floatY = Math.sin(f * 0.08) * 5;
  const blockH = 44 + lines.length * lh + 20;
  const topY = Math.round(zoneMid(isLand) - blockH / 2 + floatY);
  const leftX = isLand ? 220 : 76;

  return (
    <MotionStage dur={dur}>
      <div style={{
        position: "absolute", left: leftX - 30, right: isLand ? 220 : 60, top: topY - 30, height: blockH + 60,
        background: "linear-gradient(90deg, rgba(6,10,8,0.72) 0%, rgba(6,10,8,0.35) 75%, rgba(6,10,8,0) 100%)",
        borderRadius: 24,
      }} />

      <div style={{position: "absolute", left: leftX, right: isLand ? 240 : 76, top: topY}}>
        {/* Garis vertikal menyala yang tumbuh dari atas ke bawah */}
        <div style={{
          position: "absolute", left: 0, top: 0, width: 8, borderRadius: 4,
          height: blockH * barGrow,
          background: `linear-gradient(180deg, ${C.orange} 0%, ${C.cream} 100%)`,
          boxShadow: `0 0 22px ${C.orange}`,
        }} />

        <div style={{paddingLeft: 32}}>
          <WipeReveal delay={2}>
            <div style={{
              fontFamily: F_COND, fontWeight: 700, fontSize: 24, letterSpacing: 5,
              color: C.orange, marginBottom: 10,
            }}>
              {badge}
            </div>
          </WipeReveal>

          {lines.map((ln, i) => (
            <WipeReveal key={i} delay={5 + i * 4}>
              <div style={{
                fontWeight: 800, fontSize: fs, lineHeight: `${lh}px`, color: C.cream,
                WebkitTextStroke: `5px ${C.black}`, paintOrder: "stroke fill",
                textShadow: "0 6px 24px rgba(0,0,0,0.85)",
              } as any}>
                <HighlightedSentence text={ln} delay={11 + i * 4} color={C.orange} ink={C.black} />
              </div>
            </WipeReveal>
          ))}
        </div>
      </div>
    </MotionStage>
  );
};

// ----------------------------------------------------------------------------
// 3. FLOATING_CARD: Kartu bukti/angka mengambang 3D dengan light-sweep & tilt
// ----------------------------------------------------------------------------
const FloatingCard: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const {glass} = useContext(CfgCtx);
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;

  const entry = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR_FLOAT})));
  // Gerakan mengambang organik terus-menerus (tidak pernah diam kaku)
  const bobY = Math.sin(f * 0.11) * 9;
  const tiltDeg = Math.cos(f * 0.07) * 1.4 - (1 - entry) * 6;
  const sweepX = interpolate(f, [8, 34], [-120, 220], {...clamp, easing: Easing.inOut(Easing.quad)});

  const valueTxt = up(o.value || o.title || o.text || (Array.isArray(o.lines) ? o.lines[0] : "DATA"));
  const subTxt = up(o.label || o.sub || (Array.isArray(o.lines) && o.lines[1] ? o.lines[1] : ""));
  const badgeTxt = up(o.badge || "SOROTAN DATA");

  const cardW = isLand ? 1060 : 880;
  const cardH = isLand ? 360 : 420;
  const cardX = Math.round((width - cardW) / 2);
  const cardY = Math.round(zoneMid(isLand) - cardH / 2);
  const from = Math.round(((o && o.t0) || 0) * fps);

  const vLines = wrapLines([valueTxt], isLand ? 20 : 13).slice(0, 2).map((l) => l.replace(/^\*/, ""));
  const vSize = fitSize(vLines.reduce((a, b) => (a.length >= b.length ? a : b), ""), isLand ? 106 : 114, cardW - 100);

  return (
    <MotionStage dur={dur}>
      <div style={{
        position: "absolute", left: cardX, top: cardY, width: cardW, height: cardH,
        borderRadius: 32, overflow: "hidden",
        transform: `translateY(${(1 - entry) * 90 + bobY}px) rotate(${tiltDeg}deg) scale(${0.88 + entry * 0.12})`,
        opacity: Math.min(1, entry * 1.6),
        background: "rgba(10,14,12,0.52)",
        backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)",
        border: `2px solid rgba(255,255,255,0.24)`,
        boxShadow: "0 28px 70px rgba(0,0,0,0.65), 0 0 0 1px rgba(var(--a),0.35)",
      } as any}>
        {glass ? (
          <div style={{position: "absolute", left: -cardX, top: -cardY, width, height}}>
            <OffthreadVideo src={staticFile(glass)} startFrom={from} style={{width, height, objectFit: "cover"}} />
          </div>
        ) : null}
        <div style={{
          position: "absolute", inset: 0,
          background: "linear-gradient(135deg, rgba(var(--d),0.72) 0%, rgba(8,12,10,0.82) 100%)",
        }} />

        {/* Kilau cahaya melintas di permukaan kartu */}
        <div style={{
          position: "absolute", top: -40, bottom: -40, width: 140,
          left: `${sweepX}%`,
          background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent)",
          transform: "skewX(-22deg)",
        }} />

        <div style={{
          position: "relative", height: "100%", padding: "32px 44px", boxSizing: "border-box",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center",
        }}>
          <div style={{
            padding: "6px 18px", borderRadius: 999,
            background: C.orange, color: C.black,
            fontFamily: F_COND, fontWeight: 700, fontSize: 22, letterSpacing: 4,
            marginBottom: 18,
          }}>
            {badgeTxt}
          </div>

          {vLines.map((ln, i) => (
            <WipeReveal key={i} delay={4 + i * 3}>
              <div style={{
                fontFamily: F_SERIF, fontStyle: "italic", fontWeight: 700,
                fontSize: vSize, lineHeight: 1.12, color: C.cream,
                textShadow: "0 6px 24px rgba(0,0,0,0.7)",
              }}>
                {ln}
              </div>
            </WipeReveal>
          ))}

          {subTxt ? (
            <WipeReveal delay={10} style={{marginTop: 14}}>
              <div style={{
                fontFamily: F_DISPLAY, fontWeight: 700, fontSize: isLand ? 28 : 32,
                color: C.cream, opacity: 0.9, letterSpacing: 1,
              }}>
                {subTxt}
              </div>
            </WipeReveal>
          ) : null}
        </div>
      </div>
    </MotionStage>
  );
};

// ----------------------------------------------------------------------------
// 4. CAMERA_BEAT: Bingkai sinematik hidup & pulse fokus di atas B-roll
// ----------------------------------------------------------------------------
const CameraBeat: React.FC<any> = ({o, dur}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLand = width > height;
  const Z = ZONE(isLand);

  const snap = Math.min(1, Math.max(0, spring({frame: f, fps, config: SPR_SNAP})));
  const pulse = 1 + Math.sin(f * 0.25) * 0.015;
  const label = up(o.label || o.text || o.kicker || "");

  const padX = isLand ? 120 : 64;
  const topY = Z.top + 20;
  const boxH = Z.bottom - Z.top - 40;
  const corner = 44;

  return (
    <MotionStage dur={dur} driftDir={0}>
      {/* Bingkai sudut kamera (viewfinder brackets) yang mengunci subjek */}
      <div style={{
        position: "absolute", left: padX, right: padX, top: topY, height: boxH,
        transform: `scale(${(1.12 - snap * 0.12) * pulse})`,
        opacity: snap * 0.92,
      }}>
        {/* Kiri Atas */}
        <div style={{position: "absolute", left: 0, top: 0, width: corner, height: corner, borderTop: `5px solid ${C.orange}`, borderLeft: `5px solid ${C.orange}`}} />
        {/* Kanan Atas */}
        <div style={{position: "absolute", right: 0, top: 0, width: corner, height: corner, borderTop: `5px solid ${C.orange}`, borderRight: `5px solid ${C.orange}`}} />
        {/* Kiri Bawah */}
        <div style={{position: "absolute", left: 0, bottom: 0, width: corner, height: corner, borderBottom: `5px solid ${C.orange}`, borderLeft: `5px solid ${C.orange}`}} />
        {/* Kanan Bawah */}
        <div style={{position: "absolute", right: 0, bottom: 0, width: corner, height: corner, borderBottom: `5px solid ${C.orange}`, borderRight: `5px solid ${C.orange}`}} />

        {label ? (
          <div style={{
            position: "absolute", left: 20, top: 16,
            display: "flex", alignItems: "center", gap: 10,
            background: "rgba(0,0,0,0.58)", padding: "6px 14px", borderRadius: 8,
          }}>
            <div style={{
              width: 10, height: 10, borderRadius: "50%", background: C.orange,
              opacity: Math.sin(f * 0.35) > 0 ? 1 : 0.25,
            }} />
            <span style={{fontFamily: F_COND, fontWeight: 700, fontSize: 22, letterSpacing: 3, color: C.cream}}>
              {label}
            </span>
          </div>
        ) : null}
      </div>
    </MotionStage>
  );
};

// ----------------------------------------------------------------------------
// SUBTITLE WHISPER (Zona Bawah Terkunci)
// ----------------------------------------------------------------------------
const Caps: React.FC<any> = ({cue, dur, fps}) => {
  const C = useC();
  const f = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const isLand = width > height;
  const out = interpolate(f, [dur - 4, dur], [1, 0], clamp);
  const inn = interpolate(f, [0, 4], [0, 1], clamp);
  const longest = Math.max(...cue.lines.map((ln: any[]) => ln.map((x: any) => x.w).join(" ").length), 1);
  const fs = Math.max(isLand ? 28 : 32, Math.min(isLand ? 40 : 50, (isLand ? 1400 : 840) / (longest * 0.66)));
  const lh = Math.round(fs * 1.3);
  return (
    <AbsoluteFill style={{fontFamily: F_DISPLAY, opacity: out * inn}}>
      <div style={{position: "absolute", left: "8%", width: "84%", bottom: height - ZONE(isLand).sub, display: "flex", justifyContent: "center"}}>
        <div style={{
          maxWidth: "100%", boxSizing: "border-box",
          display: "flex", flexDirection: "column", alignItems: "center", gap: 2, textAlign: "center",
        }}>
          {cue.lines.map((ln: any[], li: number) => (
            <div key={li} style={{display: "flex", justifyContent: "center", gap: fs * 0.28, whiteSpace: "nowrap"}}>
              {ln.map((wd: any, wi: number) => {
                const st = Math.round((wd.t0 - cue.s) * fps);
                const sp = spring({frame: f - st, fps, config: {damping: 18, stiffness: 190, mass: 0.6}});
                const pp = Math.min(1, Math.max(0, sp));
                return (
                  <div key={wi} style={{
                    fontWeight: 600, fontSize: fs, lineHeight: `${lh}px`,
                    color: wd.kw ? C.orange : "#FFFFFF",
                    opacity: f >= st ? Math.min(1, pp * 2.2) : 0,
                    transform: `translateY(${(1 - pp) * 8}px)`,
                    textShadow: SUB_SHADOW,
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

// Registrasi komponen baru + pemetaan otomatis jika ada tipe lama dari cache
const REG: any = {
  roto_typo: RotoTypo,
  draw_on: DrawOn,
  stat_counter: StatCounter,
  kinetic_typo: KineticTypo,
  marker_quote: MarkerQuote,
  floating_card: FloatingCard,
  camera_beat: CameraBeat,
  // Fallback kompatibilitas plan lama:
  big: KineticTypo,
  lower: MarkerQuote,
  quote: MarkerQuote,
  stat: FloatingCard,
  list: FloatingCard,
  solid: KineticTypo,
  icon: CameraBeat,
  qmark: KineticTypo,
  map: MarkerQuote,
};

export const Overlay: React.FC<any> = ({overlays = [], fps = 30, images = [], captions = [], theme = null, glassSrc = null}) => {
  const C = useMemo(() => ({...DEFAULT_C, ...(theme && theme.hex ? theme.hex : {})}), [theme]);
  const vars: any = theme && theme.vars ? theme.vars : {};
  const cfg = useMemo(() => ({glass: glassSrc}), [glassSrc]);
  return (
    <ThemeCtx.Provider value={C}>
      <CfgCtx.Provider value={cfg}>
        <AbsoluteFill style={vars}>
          <CameraRig>
          <CameraMotionBlur samples={6} shutterAngle={150}>
            {overlays.map((o: any, i: number) => {
              const from = Math.round(o.t0 * fps);
              const dur = Math.max(6, Math.round((o.t1 - o.t0) * fps));
              const Comp = REG[o.type] || KineticTypo;
              return (
                <Sequence key={i} from={from} durationInFrames={dur}>
                  <Comp o={o} dur={dur} images={images} />
                </Sequence>
              );
            })}
          </CameraMotionBlur>
          </CameraRig>
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
    _mb_dir = os.path.join(None, "node_modules", "@remotion", "motion-blur")
    if not os.path.isdir(_mb_dir):
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund",
             "@remotion/motion-blur@4.0.352", "@remotion/paths@4.0.352", "@remotion/transitions@4.0.352"],
            cwd=None, check=True
        )
    return None