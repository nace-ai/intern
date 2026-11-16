import { Component, type ReactNode } from "react";

interface Props {
  name: string;
  children: ReactNode;
}

interface State {
  message: string | null;
}

/** Keeps a crash in one panel from blanking the whole page. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(error: unknown): State {
    return { message: error instanceof Error ? error.message : String(error) };
  }

  componentDidCatch(error: unknown): void {
    console.error(`panel "${this.props.name}" failed`, error);
  }

  render(): ReactNode {
    if (this.state.message !== null) {
      return (
        <div className="muted-note" role="alert" style={{ padding: 12 }}>
          {this.props.name} failed: {this.state.message}
        </div>
      );
    }
    return this.props.children;
  }
}
