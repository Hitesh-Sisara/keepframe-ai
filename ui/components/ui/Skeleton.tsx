/** Shimmer placeholder. Respects prefers-reduced-motion (static fill). */
export function Skeleton({ className = "h-4 w-full", rounded }: { className?: string; rounded?: "full" | "md" }) {
  return <span aria-hidden="true" className={`kf-skeleton block ${rounded === "full" ? "!rounded-full" : ""} ${className}`} />;
}

export function SkeletonText({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <span aria-hidden="true" className={`flex flex-col gap-2 ${className}`}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-3 ${i === lines - 1 ? "w-3/5" : "w-full"}`} />
      ))}
    </span>
  );
}
