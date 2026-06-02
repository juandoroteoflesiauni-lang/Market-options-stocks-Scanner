import { NavigationItem } from "./NavigationItem";

export function TopNavigationBar() {
  return (
    <nav className="sticky top-0 z-50 w-full glass-panel">
      <div className="flex h-14 items-center px-6">
        <div className="flex items-center gap-8">
          <span className="font-semibold text-lg tracking-tight">Deep Funnel Station</span>
          <div className="hidden md:flex gap-6">
            <NavigationItem label="Dashboard" href="/" />
          </div>
        </div>
        <div className="ml-auto flex items-center gap-4">
          <div className="h-2 w-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]" title="System Online" />
        </div>
      </div>
    </nav>
  );
}
