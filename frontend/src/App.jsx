import React, { useEffect, Suspense, lazy } from "react";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { NavBar } from "./components/layout/NavBar";
import { AlertBar, EdgeGlow, StormAlertOverlay } from "./components/alerts/index";
import { useStormStore } from "./store/useStormStore";
import { useSolarData, useKpForecast, useSatelliteRisk, useGridRisk, useAdvisory, useSocket, useShapExplain } from "./hooks/index";
import { PageSkeleton } from "./components/ui/index";

const Dashboard  = lazy(() => import("./pages/Dashboard"));
const StormSim   = lazy(() => import("./pages/StormSim"));
const Satellites = lazy(() => import("./pages/Satellites"));
const GridMap    = lazy(() => import("./pages/GridMap"));
const Replay     = lazy(() => import("./pages/Replay"));
const Advisory   = lazy(() => import("./pages/Advisory"));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry:2, staleTime:30_000 } },
});

const pageVariants = {
  initial: { opacity:0, y:10 },
  animate: { opacity:1, y:0,  transition:{ duration:0.4, ease:[0.25,0.1,0.25,1] } },
  exit:    { opacity:0, y:-6, transition:{ duration:0.22 } },
};

function DataBootstrap() {
  useSolarData();
  useKpForecast();
  useSatelliteRisk();
  useGridRisk();
  useAdvisory();
  useShapExplain();
  useSocket();
  const { solarWind, showAlertOverlay, alertDismissed } = useStormStore();
  useEffect(() => {
    if (solarWind?.kp_current >= 7 && !alertDismissed) showAlertOverlay();
  }, [solarWind?.kp_current, solarWind?.storm_class, alertDismissed, showAlertOverlay]);
  return null;
}

function AnimatedRoutes() {
  const location = useLocation();
  // Don't animate StormSim (full screen 3D)
  const noAnim = location.pathname === "/storm-sim";
  return (
    <AnimatePresence mode="wait">
      <motion.div key={location.pathname}
        variants={noAnim ? {} : pageVariants}
        initial="initial" animate="animate" exit="exit">
        <Suspense fallback={<PageSkeleton />}>
          <Routes location={location}>
            <Route path="/"           element={<Dashboard />}  />
            <Route path="/storm-sim"  element={<StormSim />}   />
            <Route path="/satellites" element={<Satellites />} />
            <Route path="/grid"       element={<GridMap />}    />
            <Route path="/replay"     element={<Replay />}     />
            <Route path="/advisory"   element={<Advisory />}   />
          </Routes>
        </Suspense>
      </motion.div>
    </AnimatePresence>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <DataBootstrap />
        <NavBar />
        <AlertBar />
        <EdgeGlow />
        <StormAlertOverlay />
        <AnimatedRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
