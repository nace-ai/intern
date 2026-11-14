import { Component, type ReactNode } from "react";

let cached: boolean | null = null;
/**
 * True when the browser can create a WebGL context. Probed ONCE and the probe context is released immediately:
 * browsers cap live WebGL contexts (~16) and evict the oldest, so probing per render would kill the real scene.
 */
export function webglAvailable(): boolean {
  if (cached !== null) return cached;
  try {
    const c = document.createElement("canvas");
    const gl = (c.getContext("webgl2") ?? c.getContext("webgl")) as WebGLRenderingContext | null;
    cached = gl !== null;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    cached = false;
  }
  return cached;
}

interface Props { fallback: ReactNode; onError?: (error: unknown) => void; children: ReactNode }
interface State { failed: boolean }

/** Optional wrapper for the lazy 3D view: any render/load error or missing WebGL shows `fallback` (the 2D city) instead. */
export class SceneBoundary extends Component<Props, State> {
  state: State = { failed: !webglAvailable() };
  static getDerivedStateFromError(): State { return { failed: true }; }
  componentDidCatch(error: unknown): void {
    console.warn("[city3d] 3D view failed, falling back to 2D:", error);
    this.props.onError?.(error);
  }
  componentDidMount(): void {
    if (this.state.failed) this.props.onError?.(new Error("WebGL unavailable"));
  }
  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
