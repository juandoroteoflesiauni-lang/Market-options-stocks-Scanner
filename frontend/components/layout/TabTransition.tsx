"use client";
import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";
import { useTabStore } from "@/store/tabStore";

interface Props {
  tabKey: string;
  children: ReactNode;
}

export function TabTransition({ tabKey, children }: Props) {
  const { direction } = useTabStore();
  const xIn = 24 * direction;
  const xOut = -24 * direction;

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={tabKey}
        initial={{ opacity: 0, x: xIn, filter: "blur(4px)" }}
        animate={{
          opacity: 1,
          x: 0,
          filter: "blur(0px)",
          transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] },
        }}
        exit={{
          opacity: 0,
          x: xOut,
          filter: "blur(4px)",
          transition: { duration: 0.18 },
        }}
        className="h-full"
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}

/** Staggered container for cards within a tab */
export const StaggerContainer = motion.div;
export const StaggerCard = motion.div;

export const staggerContainerProps = {
  initial: "hidden",
  animate: "visible",
  variants: {
    hidden: {},
    visible: { transition: { staggerChildren: 0.04 } },
  },
};

export const staggerCardProps = {
  variants: {
    hidden: { opacity: 0, y: 16 },
    visible: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.28, ease: [0.16, 1, 0.3, 1] as const },
    },
  },
};
