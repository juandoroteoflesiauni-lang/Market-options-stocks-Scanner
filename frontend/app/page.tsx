export default function HomePage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-3.5rem)] px-4">
      <h1 className="text-4xl font-bold tracking-tight sm:text-6xl text-center mb-6 text-white">
        Deep Funnel Station
      </h1>
      <p className="mt-4 text-lg text-gray-400 max-w-2xl text-center">
        System initialized. Waiting for Phase 2 unlock to connect to market streams and display execution signals.
      </p>
    </div>
  );
}
