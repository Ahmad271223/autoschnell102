import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import Chancen from "@/pages/app/Chancen";

/** Admin → Marktanalyse → Chancen (Deal Radar, Auftrag v2 Nr. 36). */
export default function MarktChancen() {
  return (
    <div>
      <Link to="/admin/markt" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Marktanalyse</Link>
      <Chancen admin pfad="/admin/market/opportunities" modellePfad="/admin/market/models" />
    </div>
  );
}
