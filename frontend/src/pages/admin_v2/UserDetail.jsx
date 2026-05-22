import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { ArrowLeft, FileText, Crown, Mail, Building2, Calendar, Download, Eye } from "lucide-react";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate, fmtNum } from "./_ui";

export default function AdminUserDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get(`/admin/users/${id}/contracts`);
      setData(r.data);
    } catch (e) {
      toast.error(errMsg(e, "Fehler beim Laden"));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  if (loading) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  if (!data) return <EmptyState title="Nutzer nicht gefunden" />;
  const u = data.user || {};
  const contracts = data.contracts || [];

  const openPdf = async (c) => {
    try {
      const r = await api.get(`/admin/contracts/${c.id}/pdf`, { responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) { toast.error(errMsg(e, "PDF nicht verfügbar")); }
  };

  return (
    <div>
      <Link to="/admin/users" className="inline-flex items-center gap-1.5 text-[13px] text-zinc-400 hover:text-white mb-3">
        <ArrowLeft size={14} /> Zurück zu Nutzern
      </Link>
      <PageHeader
        title={u.company_name || u.username || u.email}
        subtitle="Nutzerprofil & Verträge (read-only)"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            {u.is_super_admin && <Crown size={16} className="text-amber-400" />}
            <span className="text-[15px] font-semibold text-white">Profil</span>
          </div>
          <Row icon={<Mail size={14} />}     label="E-Mail"        value={u.email} />
          <Row icon={<Building2 size={14} />} label="Firma"        value={u.company_name || "—"} />
          <Row icon={<Calendar size={14} />}  label="Erstellt"      value={fmtDate(u.created_at)} />
          <Row label="Rolle"        value={<Badge tone={u.role === "admin" ? "purple" : "gray"}>{u.role || "dealer"}</Badge>} />
          <Row label="Status"       value={u.active === false
            ? <Badge tone="red">Gesperrt</Badge>
            : <Badge tone="green">Aktiv</Badge>} />
          {u.username && <Row label="Benutzername" value={u.username} />}
        </Card>

        <Card className="lg:col-span-2" padded={false}>
          <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-zinc-500" />
              <span className="text-[15px] font-semibold text-white">Verträge</span>
              <Badge>{fmtNum(contracts.length)}</Badge>
            </div>
            <span className="text-[12px] text-zinc-500">read-only · keine Bearbeitung</span>
          </div>
          {contracts.length === 0 ? (
            <EmptyState title="Noch keine Verträge" hint="Dieser Nutzer hat bisher keine Verträge erzeugt." />
          ) : (
            <ul className="divide-y" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
              {contracts.map((c) => (
                <li key={c.id} className="px-5 py-3 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-[14px] font-medium text-white truncate">
                      {c.contract_data?.buyer_name || c.contract_data?.seller_name || c.filename || "Vertrag"}
                    </div>
                    <div className="text-[12px] text-zinc-400 truncate">
                      {c.contract_data?.vehicle_make || ""} {c.contract_data?.vehicle_model || ""} · {fmtDate(c.created_at)}
                    </div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => openPdf(c)}>
                    <Eye size={14} /> Ansicht
                  </Button>
                  <a
                    href="#"
                    onClick={(e) => { e.preventDefault(); openPdf(c); }}
                    className="text-zinc-500 hover:text-white" title="PDF"
                  >
                    <Download size={16} />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function Row({ icon, label, value }) {
  return (
    <div className="flex items-start justify-between py-2 gap-3 text-[13.5px]">
      <div className="flex items-center gap-1.5 text-zinc-400">{icon}{label}</div>
      <div className="text-white text-right break-words max-w-[60%]">{value || "—"}</div>
    </div>
  );
}
