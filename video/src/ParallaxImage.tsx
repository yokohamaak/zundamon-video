import { useEffect, useRef, useState } from "react";
import { continueRender, delayRender, staticFile } from "remotion";

// 2.5Dパララックス：静止画＋深度マップを使い、深度に応じてピクセルをずらして
// 「奥行きのあるカメラ移動」を作る（静止画を動画らしく見せる）。生WebGL・追加依存なし。
//
// progress(0→1)でカメラがゆっくり寄り＋ドリフト。深度の差で近景/遠景がずれて立体的に動く。
// 深度マップ(grayscale: 明=近 / 暗=遠)が無ければ親側で<Img>にフォールバックする想定。
//
// cover: 画像を枠に合わせてカバー（はみ出しは切る）。枠アスペクトに対しUVをスケールして実現。

const VERT = `attribute vec2 p; varying vec2 v; void main(){ v=vec2(p.x*0.5+0.5, 0.5-p.y*0.5); gl_Position=vec4(p,0.0,1.0); }`;

// 深度変位＋カバー＋ズーム。uImgScaleで枠アスペクトにカバー、uZoomで寄り、uCam*depthで視差。
const FRAG = `precision highp float;
varying vec2 v;
uniform sampler2D uImage;
uniform sampler2D uDepth;
uniform vec2 uImgScale;  // cover用UVスケール(<=1の軸を切り取る)
uniform float uZoom;     // >1で寄り
uniform vec2 uCam;       // カメラ移動量(uv単位)
uniform float uAmp;      // 視差強度
void main(){
  vec2 c = 0.5 + (v - 0.5) * uImgScale / uZoom;     // カバー＋ズーム後の基準UV
  float d = texture2D(uDepth, c).r;                  // 0=遠,1=近
  vec2 suv = c + uCam * (d - 0.5) * uAmp;            // 近いほど大きく動く＝視差
  suv = clamp(suv, 0.001, 0.999);
  gl_FragColor = texture2D(uImage, suv);
}`;

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const s = gl.createShader(type)!;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  return s;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

function makeTexture(gl: WebGLRenderingContext, img: HTMLImageElement) {
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
  return tex;
}

type GL = {
  gl: WebGLRenderingContext;
  prog: WebGLProgram;
  imgTex: WebGLTexture;
  depthTex: WebGLTexture;
  imgAspect: number;
};

export const ParallaxImage: React.FC<{
  image: string;       // staticFile相対パス
  depth: string;       // 深度マップ staticFile相対パス
  progress: number;    // 0→1（カット内進捗）
  boxW: number;
  boxH: number;
  intensity?: number;  // 視差強度の倍率
  filter?: string;     // CSS filter
}> = ({ image, depth, progress, boxW, boxH, intensity = 1, filter }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const glRef = useRef<GL | null>(null);
  const [ready, setReady] = useState(false);

  // テクスチャ・プログラムの初期化（画像/深度のロード）。
  useEffect(() => {
    const h = delayRender("parallax-init");
    let alive = true;
    (async () => {
      const canvas = canvasRef.current;
      const gl = canvas?.getContext("webgl", { premultipliedAlpha: false });
      if (!canvas || !gl) {
        continueRender(h);
        return;
      }
      const [img, dep] = await Promise.all([
        loadImage(staticFile(image)),
        loadImage(staticFile(depth)),
      ]);
      if (!alive) {
        continueRender(h);
        return;
      }
      const prog = gl.createProgram()!;
      gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(prog);
      gl.useProgram(prog);
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, "p");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      glRef.current = {
        gl,
        prog,
        imgTex: makeTexture(gl, img)!,
        depthTex: makeTexture(gl, dep)!,
        imgAspect: img.naturalWidth / img.naturalHeight,
      };
      setReady(true);
      continueRender(h);
    })().catch(() => continueRender(h));
    return () => {
      alive = false;
    };
  }, [image, depth]);

  // フレームごとの描画（progress でカメラを動かす）。
  useEffect(() => {
    if (!ready) return;
    const h = delayRender("parallax-draw");
    const ctx = glRef.current;
    if (!ctx) {
      continueRender(h);
      return;
    }
    const { gl, prog, imgTex, depthTex, imgAspect } = ctx;
    gl.useProgram(prog);
    // cover：枠アスペクトに対し、長い軸を切り取るUVスケール（<=1）。
    const boxAspect = boxW / boxH;
    const sx = imgAspect > boxAspect ? boxAspect / imgAspect : 1;
    const sy = imgAspect > boxAspect ? 1 : imgAspect / boxAspect;
    // カメラ：ゆっくり寄り＋横ドリフト。p=0→1で push-in、左右に小さく振る。
    const p = progress;
    const zoom = 1.06 + 0.14 * p;
    const camX = (p - 0.5) * 0.07;
    const camY = (p - 0.5) * 0.025;
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, imgTex);
    gl.uniform1i(gl.getUniformLocation(prog, "uImage"), 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, depthTex);
    gl.uniform1i(gl.getUniformLocation(prog, "uDepth"), 1);
    gl.uniform2f(gl.getUniformLocation(prog, "uImgScale"), sx, sy);
    gl.uniform1f(gl.getUniformLocation(prog, "uZoom"), zoom);
    gl.uniform2f(gl.getUniformLocation(prog, "uCam"), camX, camY);
    gl.uniform1f(gl.getUniformLocation(prog, "uAmp"), 1.6 * intensity);
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.finish();
    continueRender(h);
  }, [ready, progress, boxW, boxH, intensity]);

  return (
    <canvas
      ref={canvasRef}
      width={Math.round(boxW)}
      height={Math.round(boxH)}
      style={{ width: "100%", height: "100%", display: "block", filter }}
    />
  );
};
