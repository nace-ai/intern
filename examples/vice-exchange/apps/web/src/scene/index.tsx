import { Component, lazy, Suspense, type ReactNode } from "react";
import { CanvasCity } from "./canvas/CanvasCity";
import type { CityViewProps } from "./types";

export type { CityViewProps, SelectKind } from "./types";

// CITY3D ships scene/three/ThreeCity.tsx (default export taking CityViewProps). Until it exists, or if it
// throws at runtime, the 2D canvas renders instead.
const threeModules = import.meta.glob<{ default: (p: CityViewProps) => ReactNode }>(["./three/index.tsx", "./three/ThreeCity.tsx"]);
const loadThree = threeModules["./three/index.tsx"] ?? threeModules["./three/ThreeCity.tsx"];
const ThreeCity = loadThree ? lazy(loadThree) : null;
export const has3D = ThreeCity !== null;

class SceneBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(err: unknown) { console.error("[scene] 3D renderer failed, using 2D", err); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function CityView({ renderer = "2d", ...props }: CityViewProps & { renderer?: "2d" | "3d" }) {
  const flat = <CanvasCity {...props} />;
  if (renderer === "3d" && ThreeCity) {
    return (
      <SceneBoundary fallback={flat}>
        <Suspense fallback={flat}><ThreeCity {...props} /></Suspense>
      </SceneBoundary>
    );
  }
  return flat;
}
