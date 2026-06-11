"use client";
import { TABS } from "@/types";
import { useTabStore } from "@/store/tabStore";
import { cn } from "../ui/utils";

export function TabBar() {
  const { activeTab, setTab } = useTabStore();

  return (
    <div className="flex items-center gap-px">
      {TABS.map((tab) => {
        const isActive = tab.id === activeTab;
        return (
          <button
            key={tab.id}
            onClick={() => setTab(tab.id)}
            className={cn(
              "px-3 h-8 text-[11px] tracking-widest uppercase transition-all duration-180 rounded-sm whitespace-nowrap cursor-pointer",
              "font-mono",
              isActive
                ? "bg-[#1E2D47] text-[#E8EDF5] border border-[rgba(0,195,255,0.30)] shadow-[0_0_8px_rgba(0,195,255,0.15)]"
                : "bg-transparent text-[#8B9AAF] border border-transparent hover:bg-[#1A2235] hover:text-[#E8EDF5]",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
