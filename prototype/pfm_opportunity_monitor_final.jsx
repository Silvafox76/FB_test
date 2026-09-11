import { useState, useRef } from "react";
import {
  LayoutDashboard, Database, Inbox, ClipboardCheck, Map, ScrollText, Play,
  Check, Pencil, X, ShieldCheck, Cpu, AlertTriangle, RefreshCw, Bell, Lock, Search, Globe, Languages,
  Minus, ChevronDown, ChevronRight
} from "lucide-react";

// ------------------------------------------------------------------
// Palette (matches the CCB dashboard look). Orange is reserved for the
// human checkpoint and nothing else.
// ------------------------------------------------------------------
const C = {
  bg: "#0a1020", panel: "#0d1526", card: "#121a2d", border: "#1f2a45",
  text: "#e6edf7", muted: "#8b9bb4", faint: "#5b6a86",
  blue: "#3b82f6", green: "#22c55e", amber: "#f59e0b", purple: "#8b5cf6",
  orange: "#f97316", red: "#ef4444", cyan: "#06b6d4",
};

// ------------------------------------------------------------------
// Configuration. In the real system these are versioned YAML files.
// ------------------------------------------------------------------
const WEST_AFRICA = [["BJ","Benin","fr"],["BF","Burkina Faso","fr"],["CI","Côte d'Ivoire","fr"],["GM","The Gambia","en"],["GH","Ghana","en"],["LR","Liberia","en"],["ML","Mali","fr"],["MR","Mauritania","fr, ar"],["NE","Niger","fr"],["NG","Nigeria","en"],["SN","Senegal","fr"],["SL","Sierra Leone","en"],["TG","Togo","fr"]];
const EUROPE = [["AL","Albania","sq"],["AD","Andorra","ca"],["AT","Austria","de"],["BE","Belgium","nl, fr"],["BA","Bosnia and Herzegovina","bs"],["HR","Croatia","hr"],["CY","Cyprus","el"],["DK","Denmark","da"],["EE","Estonia","et"],["FI","Finland","fi"],["FR","France","fr"],["DE","Germany","de"],["GR","Greece","el"],["IS","Iceland","is"],["IE","Ireland","en"],["IT","Italy","it"],["XK","Kosovo","sq"],["LV","Latvia","lv"],["LI","Liechtenstein","de"],["LT","Lithuania","lt"],["LU","Luxembourg","fr"],["MT","Malta","en"],["MC","Monaco","fr"],["ME","Montenegro","cnr"],["NL","Netherlands","nl"],["MK","North Macedonia","mk"],["NO","Norway","no"],["PL","Poland","pl"],["SI","Slovenia","sl"],["ES","Spain","es"],["SE","Sweden","sv"],["CH","Switzerland","de, fr"],["UA","Ukraine","uk"],["GB","United Kingdom","en"]];
const COUNTRIES = [...WEST_AFRICA.map(([iso, name, lang]) => ({ iso, name, lang, region: "West Africa" })), ...EUROPE.map(([iso, name, lang]) => ({ iso, name, lang, region: "Europe" }))];
const TED_COVERS = ["AT","BE","HR","CY","DK","EE","FI","FR","DE","GR","IS","IE","IT","LV","LI","LT","LU","MT","NL","NO","PL","SI","ES","SE"];
const WA_ISO = WEST_AFRICA.map((c) => c[0]);

const FUNCTIONS_INITIAL = [
  { id: "policy_mgmt", name: "1.1 Policy Management", pillar: "Government Performance Management", type: "Government Controls", weight: 0.97, en: "public finance legislation, public financial management, PFM policy, PFM reform, legal framework, financial management act", fr: "législation des finances publiques, gestion des finances publiques, réforme des finances publiques, cadre juridique, loi organique relative aux finances" },
  { id: "budget_planning", name: "1.2 Budget Planning / Formulation", pillar: "Government Performance Management", type: "Government Controls", weight: 1.01, en: "budget preparation, budget planning, medium-term expenditure framework, fiscal rules, budget calendar, macro-fiscal", fr: "préparation du budget, planification budgétaire, cadre de dépenses à moyen terme, règles budgétaires, calendrier budgétaire" },
  { id: "public_accounts", name: "1.3 Public Accounts Management", pillar: "Government Performance Management", type: "Government Controls", weight: 1.03, en: "government accounting standards, chart of accounts, IPSAS, public accounts, accounting framework, fiscal transparency, open budget, citizens budget, budget portal", fr: "normes comptables publiques, plan comptable, normes IPSAS, comptes publics, transparence budgétaire, budget citoyen, portail budgétaire" },
  { id: "performance_structures", name: "1.4 Performance Structures & Implementation", pillar: "Government Performance Management", type: "Oversight & Audit", weight: 1.01, en: "performance budgeting, chart of goals, results-based budgeting, output and outcome targets", fr: "budget de performance, budgétisation axée sur les résultats, objectifs de performance" },
  { id: "budget_appropriation", name: "2.1 Budget and Appropriation Control", pillar: "Government Compliance Management", type: "Government Controls", weight: 1.11, en: "appropriation control, budget control, line item budget, multi-year appropriation, apportionment", fr: "contrôle budgétaire, crédits budgétaires, autorisation de dépenses" },
  { id: "commitment_mgmt", name: "2.2 Commitment Management", pillar: "Government Compliance Management", type: "Government Controls", weight: 1.09, en: "commitment control, purchase requisition, encumbrance, pre-commitment, de-commitment", fr: "engagement des dépenses, contrôle des engagements, pré-engagement, bon de commande" },
  { id: "budget_execution", name: "2.3 Budget Execution", pillar: "Government Compliance Management", type: "Government Controls", weight: 1.11, en: "budget execution, warrant, expenditure control, virement, budget adjustment, in-year reallocation", fr: "exécution budgétaire, contrôle des dépenses, virement de crédits, régulation budgétaire" },
  { id: "compliance_governance", name: "2.4 Compliance and Governance", pillar: "Government Compliance Management", type: "Government Controls", weight: 1.13, en: "internal control, segregation of duties, workflow approval, compliance, internal audit, governance structure", fr: "contrôle interne, séparation des tâches, audit interne, gouvernance, conformité" },
  { id: "treasury_accounting", name: "3.1 Treasury Accounting", pillar: "Public Financials Management", type: "Process Execution", weight: 1.06, en: "treasury, general ledger, journal voucher, integrated financial management information system, IFMIS, GIFMIS, IFMAS, GFMAS, financial management information system", fr: "trésor, grand livre, système intégré de gestion des finances publiques, IFMAS, GFMAS, SIGIF, SIGFiP" },
  { id: "cost_accounting", name: "3.2 Cost Accounting", pillar: "Public Financials Management", type: "Process Execution", weight: 1.01, en: "cost accounting, project accounting, program budgeting, cost centre", fr: "comptabilité analytique, comptabilité de projet, centre de coût" },
  { id: "asset_inventory", name: "3.3 Asset & Inventory Management", pillar: "Public Financials Management", type: "Government Controls", weight: 1.09, en: "fixed assets, asset register, asset management, inventory management, depreciation", fr: "immobilisations, registre des actifs, gestion du patrimoine, gestion des stocks" },
  { id: "progressive_activation", name: "3.4. Progressive Activation", pillar: "Public Financials Management", type: "Approvals", weight: 0.85, en: "progressive activation, accounting basis migration, cash to accrual, phased rollout", fr: "activation progressive, transition comptabilité de caisse vers exercice, déploiement par phases" },
  { id: "bank_cash_mgmt", name: "4.1 Bank & Cash Management", pillar: "Government Treasury Management", type: "Government Controls", weight: 1.12, en: "treasury single account, TSA, cash management, bank reconciliation, cash forecasting", fr: "compte unique du trésor, gestion de la trésorerie, rapprochement bancaire, prévision de trésorerie" },
  { id: "debt_investment", name: "4.2 Debt & Investment Management", pillar: "Government Treasury Management", type: "Process Execution", weight: 1.03, en: "debt management, public debt, DMFAS, debt sustainability, sovereign investment, debt instrument", fr: "gestion de la dette, dette publique, soutenabilité de la dette, instruments de dette" },
  { id: "risk_mgmt", name: "4.3 Risk Management", pillar: "Government Treasury Management", type: "Government Controls", weight: 1.04, en: "fiscal risk, risk management, risk appetite, contingent liability", fr: "risque budgétaire, gestion des risques, passifs éventuels" },
  { id: "procurement_mgmt", name: "5.1 Procurement Management", pillar: "Public Expenditure Management", type: "Government Controls", weight: 1.09, en: "e-procurement, electronic procurement, e-GP, purchasing, procurement planning, tender management, public procurement system", fr: "dématérialisation des marchés publics, achats, système de passation des marchés, planification des achats" },
  { id: "social_benefits", name: "5.2 Social Benefits Management", pillar: "Public Expenditure Management", type: "Government Controls", weight: 1.11, en: "social benefits, employment insurance, welfare payments, eligibility rules, benefit administration", fr: "prestations sociales, assurance emploi, aide sociale, règles d’éligibilité" },
  { id: "grants_transfers", name: "5.3 Grants & Transfers Management", pillar: "Public Expenditure Management", type: "Government Controls", weight: 1.11, en: "grants management, transfers, loan guarantees, subsidies, intergovernmental transfers", fr: "gestion des subventions, transferts, garanties de prêt, subventions intergouvernementales" },
  { id: "public_investment", name: "5.4 Public Investment Management", pillar: "Public Expenditure Management", type: "Government Controls", weight: 1.08, en: "public investment management, PIM, capital budgeting, project appraisal, infrastructure pipeline", fr: "gestion de l’investissement public, budgétisation des investissements, évaluation de projets" },
  { id: "revenue_mgmt", name: "6.1 Revenue Management", pillar: "Government Receipts Management", type: "Government Controls", weight: 1.06, en: "revenue administration, taxpayer registration, revenue authority, customer information, IRMIS, revenue mobilization", fr: "administration fiscale, immatriculation des contribuables, régie financière, IRMIS, mobilisation des recettes" },
  { id: "non_tax_revenue", name: "6.2 Non-Tax Revenue", pillar: "Government Receipts Management", type: "Process Execution", weight: 1.01, en: "non-tax revenue, fees and charges, government e-commerce, sales of government services", fr: "recettes non fiscales, redevances, vente de services publics en ligne" },
  { id: "tax_revenue", name: "6.3 Tax Revenue", pillar: "Government Receipts Management", type: "Government Controls", weight: 1.05, en: "tax administration, personal income tax, ITAS, ITMIS, integrated tax administration system, tax filing, customs", fr: "administration fiscale, impôt sur le revenu, système de gestion des impôts, ITMIS, déclaration fiscale, douanes" },
  { id: "civil_service_planning", name: "7.1 Civil Service Planning", pillar: "Civil Service Management", type: "Government Controls", weight: 0.97, en: "civil service regulations, establishment control, workforce policy", fr: "réglementation de la fonction publique, contrôle des effectifs, politique des ressources humaines" },
  { id: "payroll", name: "7.2 Payroll", pillar: "Civil Service Management", type: "Process Execution", weight: 1.06, en: "payroll, IPPIS, HRMIS, HRMS, pension, pensions, pay period, salary scale, exception reporting", fr: "paie, solde, gestion de la paie, HRMS, pension, pensions, échelle salariale, fichier unique des agents" },
  { id: "workforce_mgmt", name: "7.3 Civil Service Workforce Management", pillar: "Civil Service Management", type: "Government Controls", weight: 1.06, en: "human resource management information system, HRMIS, HRMS, establishment, position management, salary scale", fr: "gestion des ressources humaines, fichier du personnel, HRMS, gestion des effectifs, grille salariale" },
  { id: "civil_service_movement", name: "7.4 Civil Service Movement", pillar: "Civil Service Management", type: "Government Controls", weight: 1.11, en: "recruitment, civil service movement, transfer, secondment, appointment", fr: "recrutement, mouvement du personnel, mutation, nomination" },
  { id: "civil_service_performance", name: "7.5 Civil Service Performance", pillar: "Civil Service Management", type: "Oversight & Audit", weight: 0.99, en: "workforce competency, skills gap, training investment, career development", fr: "compétences des agents, écart de compétences, formation, développement de carrière" },
  { id: "civil_service_benefits", name: "7.6 Civil Service Benefits Management", pillar: "Civil Service Management", type: "Process Execution", weight: 1.05, en: "civil service benefits, health insurance, life insurance, claims adjudication, self-service", fr: "avantages sociaux des agents, assurance maladie, assurance vie, libre-service" },
  { id: "g2c", name: "8.1 G2C Government to Citizen Financial Service Delivery", pillar: "Government Service Delivery", type: "Payments", weight: 0.83, en: "government to citizen payments, citizen portal, e-government payments", fr: "paiements aux citoyens, portail citoyen, paiements électroniques gouvernementaux" },
  { id: "g2b", name: "8.2 G2B Government to Business Financial Service Delivery", pillar: "Government Service Delivery", type: "Payments", weight: 0.83, en: "government to business payments, business licensing payments, supplier payments", fr: "paiements aux entreprises, paiements de licences commerciales, paiements aux fournisseurs" },
  { id: "g2n", name: "8.3 G2N Government to Non Profits Financial Service Delivery", pillar: "Government Service Delivery", type: "Payments", weight: 0.83, en: "government to non-profit payments, NGO funding disbursement", fr: "paiements aux organisations à but non lucratif, décaissement aux ONG" },
  { id: "g2e", name: "8.4 G2E Government to Employee", pillar: "Government Service Delivery", type: "Process Execution", weight: 0.98, en: "government to employee payments, employee self-service payments", fr: "paiements aux employés, libre-service employé" },
  { id: "g2g", name: "8.5 G2G Government to Government Financial Service Delivery", pillar: "Government Service Delivery", type: "Payments", weight: 0.82, en: "government to government transfers, intergovernmental fiscal transfers, sub-national transfers", fr: "transferts interministériels, transferts budgétaires infranationaux" },
];
const LEXICON_LANGS = ["en", "fr"];
const SYSTEM_NAMES = ["IFMIS", "GIFMIS", "IFMAS", "GFMAS", "IPPIS", "TSA", "HRMIS", "HRMS", "ITAS", "ITMIS", "IRMIS", "e-procurement", "SIGIF", "SIGFiP", "AGFIS", "ISFU", "SIGMAP"];
const GEOGRAPHY = Object.fromEntries([...WA_ISO.map((i) => [i, 1.0]), ["UA", 0.8], ["AL", 0.8], ["BA", 0.8], ["XK", 0.8], ["ME", 0.8], ["MK", 0.8], ["GE", 0.7]]);
const geo = (iso) => GEOGRAPHY[iso] ?? 0.6;
const PFM_CPV = ["48", "72", "79"];

// Source registry. covers: countries a multi-country source reaches. wave: 1 pilot build, 2 pilot weeks 7 to 10, 3 phase 4.
const SOURCES_INITIAL = [
  { id: "ted", name: "TED (EU and EEA)", stream: "A", country: "EU", covers: TED_COVERS, lang: "multi, en titles", connector: "Feed", access: "API (eForms)", expected: [5, 60], wave: 1, enabled: true },
  { id: "fts", name: "UK Find a Tender", stream: "A", country: "GB", lang: "en", connector: "Feed", access: "API (OCDS)", expected: [0, 10], wave: 1, enabled: true },
  { id: "prozorro", name: "Prozorro (Ukraine)", stream: "A", country: "UA", lang: "uk", connector: "Feed", access: "API (OCDS)", expected: [3, 40], wave: 1, enabled: true },
  { id: "simap", name: "simap.ch (Switzerland, federal and cantonal)", stream: "A", country: "CH", covers: ["LI"], lang: "de, fr, it", connector: "Feed", access: "API", expected: [0, 8], wave: 1, enabled: true },
  { id: "doe", name: "Datenservice Öffentlicher Einkauf (Germany, incl. Länder)", stream: "A", country: "DE", lang: "de", connector: "Feed", access: "API (eForms)", expected: [5, 80], wave: 1, enabled: true },
  { id: "place", name: "PLACE (Spain, incl. regional platforms)", stream: "A", country: "ES", lang: "es", connector: "Feed", access: "API (ATOM)", expected: [3, 50], wave: 1, enabled: true },
  { id: "boamp", name: "BOAMP (France, state and local)", stream: "A", country: "FR", lang: "fr", connector: "Feed", access: "API (open data)", expected: [3, 50], wave: 1, enabled: true },
  { id: "app-al", name: "Albania APP e-procurement", stream: "A", country: "AL", lang: "sq", connector: "Browser", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "ejn-ba", name: "Bosnia and Herzegovina EJN", stream: "A", country: "BA", lang: "bs", connector: "Browser", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "eprok-xk", name: "Kosovo e-Prokurimi", stream: "A", country: "XK", lang: "sq", connector: "Browser", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "cejn-me", name: "Montenegro CeJN", stream: "A", country: "ME", lang: "cnr", connector: "Browser", access: "Public listing", expected: [0, 8], wave: 2, enabled: true },
  { id: "enab-mk", name: "North Macedonia e-Nabavki", stream: "A", country: "MK", lang: "mk", connector: "Browser", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "tenderned", name: "TenderNed (Netherlands, below threshold)", stream: "A", country: "NL", lang: "nl", connector: "Feed", access: "API", expected: [0, 20], wave: 2, enabled: false },
  { id: "bzp", name: "e-Zamówienia (Poland, below threshold)", stream: "A", country: "PL", lang: "pl", connector: "Feed", access: "API", expected: [0, 40], wave: 2, enabled: false },
  { id: "bopa", name: "Andorra BOPA gazette", stream: "A", country: "AD", lang: "ca", connector: "Page", access: "Public listing", expected: [0, 2], wave: 3, enabled: false },
  { id: "jdm", name: "Journal de Monaco", stream: "A", country: "MC", lang: "fr", connector: "Page", access: "Public listing", expected: [0, 2], wave: 3, enabled: false },
  { id: "bj", name: "Benin SIGMAP", stream: "A", country: "BJ", lang: "fr", connector: "Page", access: "Public listing", expected: [1, 20], wave: 1, enabled: true },
  { id: "bf", name: "Burkina Faso DGMP bulletin", stream: "A", country: "BF", lang: "fr", connector: "Page", access: "PDF bulletin", expected: [1, 30], wave: 1, enabled: true },
  { id: "ci", name: "Côte d'Ivoire SIGOMAP", stream: "A", country: "CI", lang: "fr", connector: "Browser", access: "Public listing", expected: [1, 30], wave: 1, enabled: true },
  { id: "gm", name: "The Gambia GPPA", stream: "A", country: "GM", lang: "en", connector: "Page", access: "Public", expected: [0, 8], wave: 1, enabled: true },
  { id: "gh", name: "Ghana GHANEPS", stream: "A", country: "GH", lang: "en", connector: "Browser", access: "Public listing", expected: [1, 30], wave: 1, enabled: true },
  { id: "lr", name: "Liberia PPCC", stream: "A", country: "LR", lang: "en", connector: "Page", access: "Public", expected: [0, 8], wave: 1, enabled: true },
  { id: "ml", name: "Mali ARMDS", stream: "A", country: "ML", lang: "fr", connector: "Page", access: "Public listing", expected: [0, 15], wave: 1, enabled: true },
  { id: "mr", name: "Mauritania SIGMAP", stream: "A", country: "MR", lang: "fr, ar", connector: "Page", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "ne", name: "Niger ARMP", stream: "A", country: "NE", lang: "fr", connector: "Page", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "ng", name: "Nigeria BPP tenders", stream: "A", country: "NG", lang: "en", connector: "Page", access: "Public", expected: [3, 60], wave: 1, enabled: true },
  { id: "kad", name: "Kaduna State KADPPA (sub-national)", stream: "A", country: "NG", lang: "en", connector: "Page", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "lag", name: "Lagos State PPA (sub-national)", stream: "A", country: "NG", lang: "en", connector: "Page", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "sn", name: "Senegal marchespublics.sn", stream: "A", country: "SN", lang: "fr", connector: "Page", access: "Public listing", expected: [1, 25], wave: 1, enabled: true },
  { id: "sl", name: "Sierra Leone NPPA", stream: "A", country: "SL", lang: "en", connector: "Page", access: "Public", expected: [0, 8], wave: 1, enabled: true },
  { id: "tg", name: "Togo ARCOP", stream: "A", country: "TG", lang: "fr", connector: "Page", access: "Public listing", expected: [0, 10], wave: 2, enabled: true },
  { id: "wb", name: "World Bank procurement and pipeline", stream: "A, B", country: "multi", covers: [...WA_ISO, "UA", "AL", "BA", "XK", "ME", "MK"], lang: "en", connector: "Feed", access: "API", expected: [1, 20], wave: 1, enabled: true },
  { id: "afdb", name: "African Development Bank", stream: "A, B", country: "multi", covers: WA_ISO, lang: "en, fr", connector: "Page", access: "Public", expected: [0, 10], wave: 1, enabled: true },
  { id: "mcc", name: "Millennium Challenge Corporation", stream: "A, B", country: "multi", covers: ["BJ", "CI", "GH", "NE", "SN", "SL", "TG", "GM"], lang: "en", connector: "Feed", access: "Public listing", expected: [0, 5], wave: 1, enabled: true },
  { id: "ebrd", name: "EBRD procurement (ECEPP)", stream: "A, B", country: "multi", covers: ["UA", "AL", "BA", "XK", "ME", "MK"], lang: "en", connector: "Page", access: "Public", expected: [0, 10], wave: 1, enabled: true },
  { id: "euft", name: "EU Funding and Tenders (external actions)", stream: "A, B", country: "multi", covers: [...WA_ISO, "UA", "AL", "BA", "XK", "ME", "MK"], lang: "en, fr", connector: "Feed", access: "API", expected: [0, 15], wave: 1, enabled: true },
  { id: "undp", name: "UNDP procurement notices", stream: "A, B", country: "multi", covers: [...WA_ISO, "UA", "AL", "BA", "XK", "ME", "MK"], lang: "en", connector: "Feed", access: "RSS", expected: [0, 15], wave: 1, enabled: true },
  { id: "ungm", name: "UNGM", stream: "A", country: "multi", covers: [...WA_ISO, "UA"], lang: "en", connector: "Feed", access: "Registered", expected: [0, 15], wave: 1, enabled: true },
  { id: "undb", name: "UN Development Business (mailbox)", stream: "A, B", country: "multi", covers: WA_ISO, lang: "en", connector: "Mail", access: "Subscription", expected: [0, 15], wave: 1, enabled: true },
  { id: "isdb", name: "Islamic Development Bank", stream: "A, B", country: "multi", covers: ["ML", "NE", "SN", "MR", "TG", "BJ", "BF"], lang: "en, fr", connector: "Page", access: "Public", expected: [0, 5], wave: 2, enabled: true },
  { id: "boad", name: "BOAD (West African Development Bank)", stream: "A, B", country: "multi", covers: ["BJ", "BF", "CI", "ML", "NE", "SN", "TG"], lang: "fr", connector: "Page", access: "Public", expected: [0, 5], wave: 2, enabled: true },
  { id: "imf", name: "IMF, PEFA and World Bank research corpus", stream: "C", country: "multi", covers: COUNTRIES.map((c) => c.iso), lang: "en, fr", connector: "Feed", access: "Public documents", expected: [0, 5], wave: 1, enabled: false },
];

// Fixture notices. title and body are the original text; tr_title and tr_body are recorded translations used by the mock translate stage. title_en and body_en are set only at run time.
// Wave 1 arrives on the first scan, wave 2 on the second.
const N = (o) => ({ wave: 1, cpv: null, value: null, lang: "en", admin: "national", ...o });
const FIXTURES = [
  N({ id: "n01", source: "ted", externalId: "2026/S 171-482910", lang: "en", title: "Modernisation of the State Treasury information system, including treasury single account and budget execution modules", buyer: "Ministry of Finance, Republic of Latvia", country: "LV", published: "2026-09-08", deadline: "2026-10-20", cpv: "48000000", value: 4600000, url: "https://ted.europa.eu/en/notice/-/detail/482910-2026", body: "Open procedure. Supply, implementation and five years of support for a treasury management system covering cash management, treasury single account operations, budget execution and commitment control, with interfaces to the state budget planning system and the general ledger. Bidders from EU member states and WTO GPA signatories are eligible." }),
  N({ id: "n02", source: "ted", externalId: "2026/S 171-482977", title: "Road resurfacing works, municipal roads, City of Ghent", buyer: "Stad Gent", country: "BE", admin: "subnational", published: "2026-09-08", deadline: "2026-10-05", cpv: "45233220", value: 1200000, url: "https://ted.europa.eu/en/notice/-/detail/482977-2026", body: "Works contract for resurfacing of approximately 14 km of municipal roads." }),
  N({ id: "n03", source: "ted", externalId: "2026/S 171-483002", title: "Supply of surgical gloves and disposable protective equipment", buyer: "Regional hospital group, Lombardy", country: "IT", admin: "subnational", published: "2026-09-08", deadline: "2026-10-01", cpv: "33141420", value: 800000, url: "https://ted.europa.eu/en/notice/-/detail/483002-2026", body: "Framework agreement for medical consumables." }),
  N({ id: "n04", source: "ted", externalId: "2026/S 171-483115", title: "Framework agreement for ERP maintenance and consultancy services", buyer: "Federal logistics agency", country: "AT", published: "2026-09-08", deadline: "2026-10-12", cpv: "72000000", value: 2100000, url: "https://ted.europa.eu/en/notice/-/detail/483115-2026", body: "Maintenance, second and third level support and consultancy for the agency's SAP ERP estate: logistics, warehouse management, plant maintenance and the budget preparation workflow for internal cost centres. No public finance functions in scope." }),
  N({ id: "n05", source: "fts", externalId: "ocds-h6vhtk-04a1f2", title: "Provision of an integrated payroll and HR system", buyer: "Mid Sussex District Council", country: "GB", admin: "subnational", published: "2026-09-07", deadline: "2026-10-15", cpv: "48450000", value: 650000, url: "https://www.find-tender.service.gov.uk/Notice/04a1f2-2026", body: "Cloud-hosted payroll and human resource management information system for a district council of 900 staff. Local authority procurement; UK-registered suppliers." }),
  N({ id: "n06", source: "wb", externalId: "OP00312907", title: "Ghana PFM for Service Delivery Project: supply, installation and support of the GIFMIS upgrade (budget execution, TSA and e-procurement modules)", buyer: "Controller and Accountant-General's Department, Ghana", country: "GH", admin: "donor", published: "2026-09-06", deadline: "2026-10-14", value: 6800000, url: "https://projects.worldbank.org/en/projects-operations/procurement-detail/OP00312907", body: "Request for bids under IDA financing. Upgrade of the Ghana Integrated Financial Management Information System (GIFMIS) including budget execution, treasury single account reconciliation, e-procurement integration and reporting for all MDAs. Two-envelope process, international competitive bidding." }),
  N({ id: "n07", source: "gh", externalId: "GHANEPS-2026-CAGD-0091", title: "Upgrade of the Ghana Integrated Financial Management Information System (GIFMIS): treasury and budget modules", buyer: "Controller and Accountant-General's Department", country: "GH", published: "2026-09-07", deadline: "2026-10-14", url: "https://www.ghaneps.gov.gh/epps/cft/listContractDocuments.do?resourceId=0091", body: "Invitation for bids. The CAGD invites eligible bidders for the upgrade of GIFMIS covering treasury, budget execution and reporting. Bidding documents available on GHANEPS after registration. Financed by the World Bank under the PFM for Service Delivery Project." }),
  N({ id: "n08", source: "wb", externalId: "OP00312944", title: "Sierra Leone: consultancy for the design of a treasury single account roadmap and cash management framework", buyer: "Ministry of Finance, Sierra Leone", country: "SL", admin: "donor", published: "2026-09-05", deadline: "2026-09-30", value: 350000, url: "https://projects.worldbank.org/en/projects-operations/procurement-detail/OP00312944", body: "Request for expressions of interest. Consulting firm to design the TSA roadmap, cash management procedures and the interface requirements to the national IFMIS." }),
  N({ id: "n09", source: "afdb", externalId: "AfDB-LR-2026-117", title: "Liberia PFM reform support: IFMIS rollout to county treasuries, hardware, software and training", buyer: "Ministry of Finance and Development Planning, Liberia", country: "LR", admin: "donor", published: "2026-09-04", deadline: "2026-10-22", value: 3200000, url: "https://www.afdb.org/en/projects-and-operations/procurement/LR-2026-117", body: "General procurement notice. Extension of the integrated financial management information system to fifteen county treasuries, including connectivity, hardware, licences and change management." }),
  N({ id: "n10", source: "afdb", externalId: "AfDB-GM-P-Z1-KF0-045", title: "The Gambia: Public Financial Management Modernisation Project, project pipeline, appraisal stage", buyer: "African Development Bank, Country Office The Gambia", country: "GM", admin: "donor", published: "2026-09-03", deadline: null, value: 25000000, url: "https://www.afdb.org/en/projects-and-operations/pipeline/GM-045", body: "Pipeline entry. The project will finance chart of accounts reform, IFMIS extension to local government councils, internal audit strengthening and a fiscal transparency portal. Board approval expected Q1 2027. Procurement plan to follow." }),
  N({ id: "n11", source: "ebrd", externalId: "EBRD-TC-2026-54321", title: "Georgia: enhancement of the state electronic procurement system and integration with the treasury", buyer: "State Procurement Agency of Georgia", country: "GE", published: "2026-09-05", deadline: "2026-10-09", value: 900000, url: "https://www.ebrd.com/work-with-us/procurement/tc-2026-54321.html", body: "Technical cooperation. Enhancement of the national e-procurement platform, contract management module and real-time integration with treasury payment execution." }),
  N({ id: "n12", source: "ebrd", externalId: "EBRD-TC-2026-54360", title: "Ukraine: district heating energy efficiency programme, feasibility study", buyer: "Ministry of Communities and Territories Development", country: "UA", published: "2026-09-05", deadline: "2026-10-02", value: 400000, url: "https://www.ebrd.com/work-with-us/procurement/tc-2026-54360.html", body: "Feasibility study for heat network rehabilitation in three cities." }),
  N({ id: "n13", source: "undb", externalId: "UNDB-1123456", title: "Nigeria: integration services for IPPIS federal payroll with GIFMIS for ministries, departments and agencies", buyer: "Office of the Accountant-General of the Federation", country: "NG", published: "2026-09-06", deadline: "2026-10-08", value: 2400000, url: "https://devbusiness.un.org/content/nigeria-ippis-gifmis-integration-1123456", body: "Alert received by email. Invitation to tender for the integration of the Integrated Personnel and Payroll Information System with GIFMIS, including payroll journal posting, reconciliation and reporting across federal MDAs." }),
  N({ id: "n14", source: "ng", externalId: "BPP/FMF/2026/0417", title: "Invitation to tender: integration of IPPIS with GIFMIS for federal MDAs", buyer: "Office of the Accountant-General of the Federation", country: "NG", published: "2026-09-07", deadline: "2026-10-08", url: "https://www.bpp.gov.ng/tenders/BPP-FMF-2026-0417", body: "The Office of the Accountant-General of the Federation invites reputable and competent firms to tender for the integration of IPPIS payroll with the GIFMIS platform. Bidders must be registered with the Bureau of Public Procurement and present a Nigerian tax clearance certificate." }),
  N({ id: "n15", source: "ng", externalId: "BPP/KW/2026/0421", title: "Construction of 2 km access road, Ilorin East", buyer: "Kwara State Ministry of Works", country: "NG", admin: "subnational", published: "2026-09-07", deadline: "2026-09-28", url: "https://www.bpp.gov.ng/tenders/BPP-KW-2026-0421", body: "Civil works. Earthworks, drainage and asphalt surfacing." }),
  N({ id: "n16", source: "ng", externalId: "BPP/FME/2026/0423", title: "Supply of laptop computers to the Federal Ministry of Education", buyer: "Federal Ministry of Education", country: "NG", published: "2026-09-07", deadline: "2026-09-30", url: "https://www.bpp.gov.ng/tenders/BPP-FME-2026-0423", body: "Supply of 1,200 laptop computers and accessories." }),
  N({ id: "n17", source: "sl", externalId: "NPPA/NRA/2026/012", title: "Procurement of an integrated tax administration system (ITAS) for domestic tax", buyer: "National Revenue Authority, Sierra Leone", country: "SL", published: "2026-09-02", deadline: "2026-10-30", value: 1800000, url: "https://www.nppa.gov.sl/tenders/NRA-2026-012", body: "The National Revenue Authority seeks a vendor for an integrated tax administration system covering registration, filing, payment, audit case management and taxpayer accounting, with an interface to the IFMIS for revenue posting." }),
  N({ id: "n18", source: "lr", externalId: "PPCC/MOH/2026/088", title: "Procurement of office furniture for county health offices", buyer: "Ministry of Health, Liberia", country: "LR", published: "2026-09-04", deadline: "2026-09-25", url: "https://www.ppcc.gov.lr/tenders/MOH-2026-088", body: "Desks, chairs and filing cabinets for fifteen county offices." }),
  N({ id: "n19", source: "gm", externalId: "GPPA/MOFEA/2026/031", title: "Chart of accounts reform and IFMIS extension to local government councils", buyer: "Ministry of Finance and Economic Affairs, The Gambia", country: "GM", published: "2026-09-05", deadline: "2026-10-16", value: 1100000, url: "https://www.gppa.gm/tenders/MOFEA-2026-031", body: "Invitation for bids. Redesign of the chart of accounts in line with GFS 2014 and extension of the IFMIS to eight area councils, with training and support." }),
  // French, West Africa
  N({ id: "n22", source: "sn", lang: "fr", externalId: "ARCOP/DGB/2026/F_0471", title: "Acquisition et mise en œuvre d'un système intégré de gestion des finances publiques (SIGIF 2) pour l'administration centrale et les collectivités territoriales", tr_title: "Acquisition and implementation of an integrated public financial management system (SIGIF 2) for central government and local authorities", buyer: "Direction générale du Budget, Ministère des Finances et du Budget, Sénégal", country: "SN", published: "2026-09-08", deadline: "2026-10-23", value: 5200000, url: "https://www.marchespublics.sn/index.php?option=com_marches&id=F_0471", body: "Appel d'offres ouvert international. Fourniture, paramétrage et déploiement d'un système intégré de gestion des finances publiques couvrant la préparation du budget, l'exécution budgétaire, la chaîne de la dépense, la comptabilité publique et le compte unique du Trésor, avec interfaces vers la solde et le système fiscal. Financement Banque mondiale et État du Sénégal.", tr_body: "Open international tender. Supply, configuration and rollout of an integrated public financial management system covering budget preparation, budget execution, the expenditure chain, public accounting and the treasury single account, with interfaces to payroll and the tax system. Financed by the World Bank and the State of Senegal." }),
  N({ id: "n23", source: "ci", lang: "fr", externalId: "SIGOMAP-2026-MFB-2213", title: "Modernisation du système de gestion de la paie et du fichier unique des agents de l'État", tr_title: "Modernisation of the payroll system and the single register of state employees", buyer: "Ministère des Finances et du Budget, Côte d'Ivoire", country: "CI", published: "2026-09-07", deadline: "2026-10-19", value: 3100000, url: "https://www.marchespublics.ci/avis/2213", body: "Avis d'appel d'offres. Refonte du système de gestion de la solde, interconnexion avec le système intégré de gestion des finances publiques (SIGFiP) et le fichier unique de référence, contrôle de la masse salariale et reporting mensuel.", tr_body: "Invitation to tender. Redesign of the payroll management system, interconnection with the integrated public financial management system (SIGFiP) and the single reference register, wage bill control and monthly reporting." }),
  N({ id: "n24", source: "bj", lang: "fr", externalId: "F_MEF_118203", title: "Fourniture de mobilier de bureau au profit des directions départementales", tr_title: "Supply of office furniture for departmental directorates", buyer: "Ministère de l'Économie et des Finances, Bénin", country: "BJ", published: "2026-09-08", deadline: "2026-09-29", url: "https://marches-publics.bj/avis/F_MEF_118203", body: "Dossier d'appel d'offres pour la fourniture de bureaux, fauteuils et armoires.", tr_body: "Tender documents for the supply of desks, chairs and cabinets." }),
  N({ id: "n25", source: "bf", lang: "fr", externalId: "RMP-2026-1745-MEFP", title: "Recrutement d'un cabinet pour le renforcement de l'audit interne et du contrôle interne dans les ministères", tr_title: "Recruitment of a firm to strengthen internal audit and internal control across ministries", buyer: "Ministère de l'Économie, des Finances et de la Prospective, Burkina Faso", country: "BF", published: "2026-09-04", deadline: "2026-10-06", value: 280000, url: "https://www.dgmp.gov.bf/revue/2026/1745", body: "Manifestation d'intérêt. Prestations intellectuelles pour la mise en place d'une fonction d'audit interne conforme aux directives UEMOA, incluant un outil de suivi des recommandations.", tr_body: "Expression of interest. Advisory services to establish an internal audit function in line with UEMOA directives, including a recommendations tracking tool." }),
  N({ id: "n26", source: "ml", lang: "fr", externalId: "ARMDS/MTP/2026/0932", title: "Travaux de construction d'un pont sur le fleuve Niger à Ségou", tr_title: "Construction works for a bridge over the Niger River at Ségou", buyer: "Ministère des Transports et des Infrastructures, Mali", country: "ML", published: "2026-09-05", deadline: "2026-10-30", url: "https://www.armds.ml/avis/0932", body: "Appel d'offres pour travaux de génie civil.", tr_body: "Tender for civil engineering works." }),
  N({ id: "n27", source: "tg", lang: "fr", externalId: "ARCOP-TG-2026-0288", title: "Dématérialisation de la chaîne des marchés publics et interconnexion avec le système de gestion budgétaire", tr_title: "Digitalisation of the public procurement chain and interconnection with the budget management system", buyer: "Direction nationale du contrôle de la commande publique, Togo", country: "TG", published: "2026-09-06", deadline: "2026-10-12", value: 750000, url: "https://www.arcop.tg/avis/0288", body: "Appel d'offres. Plateforme de dématérialisation des marchés publics avec interface d'engagement des dépenses vers le système d'exécution budgétaire.", tr_body: "Invitation to tender. E-procurement platform with an expenditure commitment interface to the budget execution system." }),
  N({ id: "n28", source: "mr", lang: "ar", externalId: "SIGMAP-MR-2026-0417", title: "توريد وتركيب نظام معلومات الإدارة المالية المتكاملة لوزارة المالية", tr_title: "Supply and installation of an integrated financial management information system for the Ministry of Finance", buyer: "وزارة المالية، الجمهورية الإسلامية الموريتانية", country: "MR", published: "2026-09-03", deadline: "2026-10-15", value: 2900000, url: "https://sigmap.gov.mr/avis/0417", body: "إعلان مناقصة دولية لتوريد وتركيب نظام معلومات الإدارة المالية المتكاملة يشمل إعداد الميزانية وتنفيذها والخزينة والمحاسبة العامة، مع تدريب المستخدمين.", tr_body: "International tender for the supply and installation of an integrated financial management information system covering budget preparation and execution, treasury and public accounting, with user training." }),
  // Other European languages
  N({ id: "n29", source: "prozorro", lang: "uk", externalId: "UA-2026-09-08-004512-a", title: "Модернізація інформаційно-аналітичної системи управління державними фінансами (казначейське обслуговування бюджетів)", tr_title: "Modernisation of the public finance management information system (treasury servicing of budgets)", buyer: "Міністерство фінансів України", country: "UA", published: "2026-09-08", deadline: "2026-10-24", cpv: "48000000", value: 3800000, url: "https://prozorro.gov.ua/tender/UA-2026-09-08-004512-a", body: "Відкриті торги з особливостями. Розробка та впровадження модулів виконання бюджету, обліку зобов'язань та єдиного казначейського рахунку.", tr_body: "Open bidding. Development and implementation of budget execution, commitment accounting and treasury single account modules." }),
  N({ id: "n30", source: "prozorro", lang: "uk", externalId: "UA-2026-09-08-004590-a", title: "Закупівля вугілля кам'яного для опалювального сезону", tr_title: "Purchase of hard coal for the heating season", buyer: "Комунальне підприємство Теплоенерго", country: "UA", admin: "subnational", published: "2026-09-08", deadline: "2026-09-25", cpv: "09111000", url: "https://prozorro.gov.ua/tender/UA-2026-09-08-004590-a", body: "Постачання вугілля.", tr_body: "Coal supply." }),
  N({ id: "n31", source: "doe", lang: "de", externalId: "DOE-2026-SN-118842", title: "Einführung eines neuen Haushaltsmanagementsystems für den Freistaat Sachsen (Haushaltsplanung, Haushaltsvollzug, Kassenwesen)", tr_title: "Introduction of a new budget management system for the Free State of Saxony (budget planning, budget execution, treasury operations)", buyer: "Sächsisches Staatsministerium der Finanzen", country: "DE", admin: "subnational", published: "2026-09-08", deadline: "2026-10-27", cpv: "48440000", value: 7500000, url: "https://oeffentlichevergabe.de/notice/DOE-2026-SN-118842", body: "Offenes Verfahren. Lieferung, Einführung und Betrieb eines integrierten Haushalts-, Kassen- und Rechnungswesens für alle Ressorts, einschließlich Anordnungswesen und Schnittstellen zum Personalabrechnungssystem.", tr_body: "Open procedure. Supply, rollout and operation of an integrated budget, treasury and accounting system for all ministries, including payment orders and interfaces to the payroll system." }),
  N({ id: "n32", source: "doe", lang: "de", externalId: "DOE-2026-BW-118901", title: "Lieferung von Streusalz für den Winterdienst", tr_title: "Supply of road salt for winter services", buyer: "Landkreis Esslingen", country: "DE", admin: "subnational", published: "2026-09-08", deadline: "2026-09-30", cpv: "34927100", url: "https://oeffentlichevergabe.de/notice/DOE-2026-BW-118901", body: "Rahmenvertrag.", tr_body: "Framework agreement." }),
  N({ id: "n33", source: "place", lang: "es", externalId: "PLACE-2026-JA-44121", title: "Servicio de evolución del sistema integrado de gestión presupuestaria, contable y financiera de la Junta de Andalucía", tr_title: "Evolution services for the integrated budget, accounting and financial management system of the Regional Government of Andalusia", buyer: "Consejería de Economía, Hacienda y Fondos Europeos, Junta de Andalucía", country: "ES", admin: "subnational", published: "2026-09-07", deadline: "2026-10-14", cpv: "72260000", value: 2600000, url: "https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl=44121", body: "Procedimiento abierto. Mantenimiento evolutivo del sistema de gestión presupuestaria y contable, ejecución del gasto, tesorería y contabilidad patrimonial.", tr_body: "Open procedure. Evolutionary maintenance of the budget and accounting management system, expenditure execution, treasury and asset accounting." }),
  N({ id: "n34", source: "app-al", lang: "sq", externalId: "REF-2026-09-0812", title: "Sistemi i ri i thesarit dhe menaxhimit financiar të qeverisë (AGFIS 2) për Ministrinë e Financave", tr_title: "New treasury and government financial management system (AGFIS 2) for the Ministry of Finance", buyer: "Ministria e Financave, Shqipëri", country: "AL", published: "2026-09-06", deadline: "2026-10-21", value: 4100000, url: "https://www.app.gov.al/ep/Procurement/REF-2026-09-0812", body: "Procedurë e hapur ndërkombëtare. Zëvendësimi i sistemit të thesarit, moduli i ekzekutimit të buxhetit, llogaria e vetme e thesarit dhe raportimi fiskal.", tr_body: "Open international procedure. Replacement of the treasury system, budget execution module, treasury single account and fiscal reporting." }),
  N({ id: "n35", source: "ejn-ba", lang: "bs", externalId: "EJN-2026-1024-FMF", title: "Nabavka usluga održavanja i nadogradnje informacionog sistema finansijskog upravljanja (ISFU)", tr_title: "Procurement of maintenance and upgrade services for the financial management information system (ISFU)", buyer: "Federalno ministarstvo finansija, Bosna i Hercegovina", country: "BA", admin: "subnational", published: "2026-09-05", deadline: "2026-10-03", value: 420000, url: "https://www.ejn.gov.ba/Notice/1024-FMF", body: "Otvoreni postupak. Održavanje modula trezora, izvršenja budžeta i glavne knjige.", tr_body: "Open procedure. Maintenance of the treasury, budget execution and general ledger modules." }),
  N({ id: "n36", source: "enab-mk", lang: "mk", externalId: "ENAB-2026-14311", title: "Набавка на нов интегриран систем за управување со јавни финансии (IFMIS) за Министерството за финансии", tr_title: "Procurement of a new integrated public financial management system (IFMIS) for the Ministry of Finance", buyer: "Министерство за финансии, Република Северна Македонија", country: "MK", published: "2026-09-04", deadline: "2026-10-28", value: 5600000, url: "https://e-nabavki.gov.mk/PublicAccess/home.aspx#/dossie/14311", body: "Отворена постапка. Буџетско планирање, извршување на буџетот, трезорско работење и известување, со обука и петгодишно одржување.", tr_body: "Open procedure. Budget planning, budget execution, treasury operations and reporting, with training and five years of maintenance." }),
  N({ id: "n37", source: "euft", externalId: "EuropeAid/2026/181-442", title: "Support to public finance management reform in Kosovo (EU4PFM), phase II: IT component", buyer: "European Union Office in Kosovo", country: "XK", admin: "donor", published: "2026-09-05", deadline: "2026-10-31", value: 1900000, url: "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/tender-details/181-442", body: "Service contract. Design and delivery of budget preparation and public investment management modules integrated with the Kosovo Financial Management Information System (KFMIS)." }),
  N({ id: "n38", source: "kad", externalId: "KADPPA/MOF/2026/044", title: "Deployment of a state integrated financial management information system and treasury single account for Kaduna State", buyer: "Kaduna State Ministry of Finance", country: "NG", admin: "subnational", published: "2026-09-06", deadline: "2026-10-09", value: 1400000, url: "https://kadppa.kdsg.gov.ng/tenders/MOF-2026-044", body: "Invitation for bids. Supply and implementation of an IFMIS covering budget execution, treasury single account and MDA reporting, with integration to the state payroll." }),
  N({ id: "n39", source: "mcc", externalId: "MCC-TG-THR-2026-07", title: "Togo Threshold Program: tax administration modernisation, systems and change management", buyer: "Millennium Challenge Account Togo", country: "TG", admin: "donor", published: "2026-09-02", deadline: "2026-10-07", value: 1200000, url: "https://mcc.dgmarket.com/tenders/np-notice.do?noticeId=TG-THR-2026-07", body: "Request for proposals. Modernisation of domestic tax administration processes and the taxpayer information system of the Office Togolais des Recettes, with revenue posting to the treasury single account." }),
  N({ id: "n20", source: "ted", wave: 2, externalId: "2026/S 172-484301", title: "Development of the state budget planning and medium-term expenditure framework system", buyer: "Ministry of Finance, Republic of Estonia", country: "EE", published: "2026-09-09", deadline: "2026-10-28", cpv: "72212000", value: 2900000, url: "https://ted.europa.eu/en/notice/-/detail/484301-2026", body: "Design, build and support of a budget preparation system with programme budgeting, medium-term expenditure ceilings and an interface to the treasury's budget execution ledger." }),
  N({ id: "n21", source: "gh", wave: 2, externalId: "GHANEPS-2026-MOF-0102", title: "Supply of pickup vehicles for regional offices", buyer: "Ministry of Finance, Ghana", country: "GH", published: "2026-09-09", deadline: "2026-10-02", url: "https://www.ghaneps.gov.gh/epps/cft/listContractDocuments.do?resourceId=0102", body: "Supply of six double-cabin pickup vehicles." }),
  N({ id: "n40", source: "sn", wave: 2, lang: "fr", externalId: "ARCOP/DGID/2026/F_0488", title: "Acquisition d'un système de gestion des recettes fiscales et de télédéclaration", tr_title: "Acquisition of a tax revenue management and online filing system", buyer: "Direction générale des Impôts et des Domaines, Sénégal", country: "SN", published: "2026-09-09", deadline: "2026-10-30", value: 2200000, url: "https://www.marchespublics.sn/index.php?option=com_marches&id=F_0488", body: "Appel d'offres. Administration fiscale: immatriculation, déclaration en ligne, recouvrement et interface avec le Trésor.", tr_body: "Invitation to tender. Tax administration: registration, online filing, collection and interface to the Treasury." }),
];

// ------------------------------------------------------------------
// Pipeline stages. Each is a pure function with one job.
// ------------------------------------------------------------------
function hashOf(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return (h >>> 0).toString(16).padStart(8, "0"); }
const kw = (f, lang) => (f[lang] || "").split(",").map((k) => k.trim().toLowerCase()).filter(Boolean);
const enText = (n) => `${n.title_en ?? n.tr_title ?? n.title} ${n.body_en ?? n.tr_body ?? n.body}`.toLowerCase();

// Stage 1, free. CPV where present; then the lexicon of the notice language (en, fr). Other languages without CPV need a translation first.
function stageFilter(n, functions) {
  if (n.cpv && !PFM_CPV.includes(n.cpv.slice(0, 2))) return { pass: false, reason: `CPV ${n.cpv} outside 48/72/79` };
  const lang = LEXICON_LANGS.includes(n.lang) ? n.lang : (n.title_en ? "en" : null); // title_en exists only after the translate stage
  if (!lang) return { pass: null, reason: "needs translation before the lexicon filter" };
  const text = lang === "en" ? `${n.title_en ?? n.title} ${n.body_en ?? n.body}`.toLowerCase() : `${n.title} ${n.body}`.toLowerCase();
  const hits = functions.flatMap((f) => kw(f, lang).filter((k) => text.includes(k)).map((k) => ({ f: f.id, k })));
  if (hits.length === 0) return { pass: false, reason: `no PFM lexicon match (${lang})` };
  return { pass: true, reason: `${n.cpv ? `CPV ${n.cpv.slice(0, 2)}, ` : ""}lexicon ${lang}: ${[...new Set(hits.map((h) => h.k))].slice(0, 3).join(", ")}`, hits, lang };
}

function mockTranslate(n) {
  if (!n.tr_title) throw new Error(`no recorded translation for ${n.id}`);
  return { title_en: n.tr_title, body_en: n.tr_body, model: "mock", tokens_in: 0, tokens_out: 0 };
}

const sysRe = (s) => new RegExp(`(^|[^a-z])${s.toLowerCase().replace("-", "\\-")}([^a-z]|$)`);
function mockScore(n, functions, hits) {
  const text = enText(n) + " " + `${n.title} ${n.body}`.toLowerCase();
  const matched = functions.filter((f) => hits.some((h) => h.f === f.id)).map((f) => ({ function_id: f.id, evidence: hits.find((h) => h.f === f.id).k }));
  const systems = SYSTEM_NAMES.filter((s) => sysRe(s).test(text));
  const flags = [];
  if (/tax clearance|registered with the bureau|uk-registered|local authority procurement/.test(text)) flags.push("local_registration");
  if (/no public finance functions/.test(text)) flags.push("out_of_scope_signal");
  const fit = matched.reduce((a, m) => a + functions.find((f) => f.id === m.function_id).weight, 0);
  let x = 22 + Math.min(fit, 4.5) * 11 + (systems.length ? 14 : 0) + (n.value && n.value >= 1000000 ? 8 : n.value ? 3 : 0) + geo(n.country) * 8 + (n.admin === "donor" ? 6 : 0) - flags.length * 9;
  if (/no public finance functions|internal cost centres/.test(text)) x -= 30;
  const relevance = Math.max(0, Math.min(99, Math.round(x)));
  const ptype = /maintenance services|evolution services|evolutionary maintenance|maintenance and upgrade/.test(text) ? "services" : /consultancy|consulting firm|expressions of interest|roadmap|expression of interest|advisory/.test(text) ? "advisory" : /pipeline/.test(text) ? "other" : "system";
  const names = matched.map((m) => functions.find((f) => f.id === m.function_id).name.toLowerCase());
  return {
    relevance, title_en: n.title_en ?? n.tr_title ?? n.title, matched_functions: matched, system_names: systems, procurement_type: ptype,
    estimated_value_usd: n.value, eligibility_flags: flags, deadline_at: n.deadline,
    summary_en: `${n.buyer} (${n.country}) is procuring ${ptype === "advisory" ? "advisory services" : ptype === "services" ? "maintenance or evolution services" : ptype === "other" ? "a donor-financed programme" : "a system"} touching ${names.slice(0, 3).join(", ") || "general public finance"}.${systems.length ? ` Named systems: ${systems.join(", ")}.` : ""}${n.deadline ? ` Deadline ${n.deadline}.` : " No deadline stated."}${n.lang !== "en" ? ` Original notice in ${n.lang}.` : ""}`,
    confidence: relevance > 75 || relevance < 30 ? "high" : "medium", model: "mock", tokens_in: 0, tokens_out: 0,
  };
}

function validateScore(s, functions) {
  const fail = (m) => { throw new Error(`schema: ${m}`); };
  if (!Number.isInteger(s.relevance) || s.relevance < 0 || s.relevance > 100) fail("relevance must be int 0-100");
  if (typeof s.title_en !== "string" || !s.title_en) fail("title_en missing");
  if (!Array.isArray(s.matched_functions)) fail("matched_functions missing");
  const ids = new Set(functions.map((f) => f.id));
  s.matched_functions.forEach((m) => { if (!ids.has(m.function_id)) fail(`unknown function_id ${m.function_id}`); if (typeof m.evidence !== "string") fail("evidence must be string"); });
  if (!Array.isArray(s.system_names)) fail("system_names missing");
  if (!["system", "services", "advisory", "other"].includes(s.procurement_type)) fail("procurement_type enum");
  if (s.estimated_value_usd !== null && typeof s.estimated_value_usd !== "number") fail("estimated_value_usd type");
  if (!Array.isArray(s.eligibility_flags)) fail("eligibility_flags missing");
  if (typeof s.summary_en !== "string" || s.summary_en.split(/\s+/).length > 140) fail("summary_en missing or too long");
  if (!["high", "medium", "low"].includes(s.confidence)) fail("confidence enum");
  return s;
}

async function callModel(system, user, max_tokens) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "claude-sonnet-4-6", max_tokens, system, messages: [{ role: "user", content: user }] }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} from model endpoint`);
  const data = await res.json();
  const text = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
  return { parsed: JSON.parse(text.replace(/```json|```/g, "").trim()), tokens_in: data.usage?.input_tokens ?? 0, tokens_out: data.usage?.output_tokens ?? 0 };
}

async function claudeTranslate(n) {
  const { parsed, tokens_in, tokens_out } = await callModel(
    "You translate public procurement notices into English. Return ONLY a JSON object {\"title_en\": string, \"body_en\": string}. Keep system names, acronyms and organisation names as written.",
    `Language: ${n.lang}\nTitle: ${n.title}\nBuyer: ${n.buyer}\nText: ${n.body}`, 600);
  if (typeof parsed.title_en !== "string" || typeof parsed.body_en !== "string") throw new Error("schema: translation missing title_en or body_en");
  return { ...parsed, model: "claude-sonnet-4-6", tokens_in, tokens_out };
}

function buildSystemPrompt(functions) {
  return `You score public procurement and donor notices for FreeBalance, a vendor of government public financial management (PFM) software (IFMIS, treasury, budget, payroll, revenue, procurement systems). Notices may be in any language; reason on the original and write all output fields in English.
Return ONLY a JSON object, no prose, no markdown fences, with exactly these keys:
{"relevance": int 0-100, "title_en": string (the title in English; copy it if already English), "matched_functions": [{"function_id": string, "evidence": string}], "system_names": [string], "procurement_type": "system"|"services"|"advisory"|"other", "estimated_value_usd": int|null, "eligibility_flags": [string], "deadline_at": "YYYY-MM-DD"|null, "summary_en": string (max 120 words), "confidence": "high"|"medium"|"low"}
Rubric: relevance rewards (1) fit to the PFM functions below weighted by their weight, (2) named government systems (${SYSTEM_NAMES.join(", ")}), (3) stated contract value, (4) priority geography (West Africa: ${WA_ISO.join(", ")} highest; Ukraine and the Western Balkans next; EU, EEA, UK and Switzerland moderate), and penalises eligibility barriers (national-only, local registration, tax clearance) and generic ERP or non-government work. A private-sector ERP maintenance contract with no public finance scope should score under 30. An IFMIS or treasury system tender for a ministry of finance in West Africa or the Balkans should score above 80.
matched_functions must only use these function_id values:
${functions.map((f) => `- ${f.id}: ${f.name} (weight ${f.weight}; English keywords: ${f.en}; French keywords: ${f.fr})`).join("\n")}
eligibility_flags vocabulary: national_only, local_registration, consortium_required, tax_clearance_required. Use [] if none.`;
}

async function claudeScore(n, functions) {
  const { parsed, tokens_in, tokens_out } = await callModel(buildSystemPrompt(functions),
    `Language: ${n.lang}\nTitle: ${n.title}\nBuyer: ${n.buyer}\nCountry: ${n.country}\nAdmin level: ${n.admin}\nPublished: ${n.published}\nDeadline: ${n.deadline ?? "not stated"}\nCPV: ${n.cpv ?? "none"}\nStated value (USD): ${n.value ?? "not stated"}\nSource URL: ${n.url}\n\nNotice text:\n${n.body}${n.body_en && n.lang !== "en" ? `\n\nMachine translation of the text:\n${n.body_en}` : ""}`, 1000);
  validateScore(parsed, functions);
  return { ...parsed, model: "claude-sonnet-4-6", tokens_in, tokens_out };
}

const STOP = new Set(["of", "the", "and", "for", "to", "a", "an", "in", "with", "on", "system", "services", "project"]);
const tokens = (s) => new Set(s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter((t) => t && !STOP.has(t)));
function jaccard(a, b) { const A = tokens(a), B = tokens(b); const inter = [...A].filter((t) => B.has(t)).length; return inter / (A.size + B.size - inter || 1); }
const daysBetween = (a, b) => (!a || !b) ? 999 : Math.abs((new Date(a) - new Date(b)) / 86400000);

// Dedupe runs on the English rendering so a French and an English notice of the same tender can join.
function findDuplicate(notice, score, candidates) {
  for (const c of candidates) {
    if (c.country !== notice.country) continue;
    const sim = jaccard(c.title, score.title_en);
    if (sim >= 0.5) return { c, method: "fuzzy_title", score: Math.round(sim * 100) };
    const shared = score.system_names.filter((s) => c.systemNames.includes(s));
    if (shared.length && sim >= 0.15 && daysBetween(c.deadline, notice.deadline) <= 7) return { c, method: `system_name (${shared[0]}) + deadline`, score: 90 };
  }
  return null;
}

// ------------------------------------------------------------------
// CRM Opportunity preview. Mirrors Architecture v0.3 appendix E: every
// field is tagged D (derived from the notice, written with confidence),
// S (a heuristic suggestion the reviewer confirms) or B (BD judgement
// only, the same placeholder the CRM already uses). This is a display
// mapping only, computed at render time; it does not change what
// decide() stores in db.leads.
// ------------------------------------------------------------------
const SIM_TODAY = "2026-09-11"; // fixed reference date so deadline urgency reads correctly regardless of when this artifact is actually opened
function daysUntil(dateStr) {
  if (!dateStr) return null;
  return Math.ceil((new Date(dateStr) - new Date(SIM_TODAY)) / 86400000);
}
const DONOR_SOURCE = { wb: "World Bank (IDA/IBRD)", afdb: "AfDB", euft: "EU", mcc: "MCC", isdb: "IsDB", boad: "BOAD", ebrd: "EBRD", undp: "UNDP", ungm: "UNGM", undb: "UNDB-listed donor" };
const PRODUCT_MAP = {
  policy_mgmt: null, budget_planning: "Public Expenditures", public_accounts: "Core Accountability", performance_structures: "Core Accountability",
  budget_appropriation: "Public Expenditures", commitment_mgmt: "Public Expenditures", budget_execution: "Public Expenditures", compliance_governance: "Core Accountability",
  treasury_accounting: "Core Accountability", cost_accounting: "Public Expenditures", asset_inventory: "Core Accountability", progressive_activation: "Core Accountability",
  bank_cash_mgmt: "Treasury Management", debt_investment: "Treasury Management", risk_mgmt: "Treasury Management",
  procurement_mgmt: "Public Expenditures", social_benefits: "Public Expenditures", grants_transfers: "Public Expenditures", public_investment: "Public Expenditures",
  revenue_mgmt: "Revenue and Taxation", non_tax_revenue: "Revenue and Taxation", tax_revenue: "Revenue and Taxation",
  // Corrected against the validated company/domain keyword sheet: Civil Service Management (CSM) is
  // one of FreeBalance's six named domain categories (alongside GPM, PFM, PEM, GRM, GTM), not an
  // unsupported interface-only area as the earlier appendix E draft assumed.
  civil_service_planning: "Civil Service Management", payroll: "Civil Service Management", workforce_mgmt: "Civil Service Management",
  civil_service_movement: "Civil Service Management", civil_service_performance: "Civil Service Management", civil_service_benefits: "Civil Service Management",
  g2c: "Treasury Management", g2b: "Treasury Management", g2n: "Treasury Management", g2e: "Treasury Management", g2g: "Treasury Management",
};
const regionOf = (iso) => (WA_ISO.includes(iso) ? "North & West Africa" : "Europe");

function fundingAndPartners(candidate, notices, srcName) {
  const linked = candidate.noticeIds.map((id) => notices.find((n) => n.id === id)).filter(Boolean);
  const donorIds = [...new Set(linked.map((n) => n.source))].filter((id) => DONOR_SOURCE[id]);
  const funding = donorIds.length
    ? DONOR_SOURCE[donorIds[0]] + (donorIds.length > 1 ? ` +${donorIds.length - 1} more` : "")
    : candidate.admin === "donor" ? "Donor-financed (source unspecified)" : "Government budget";
  const partners = donorIds.map((id) => (srcName(id) || "").split(" (")[0]).filter(Boolean);
  return { funding, partners };
}

// Returns an array of { title, fields: [{label, cat, value}], more: [...] } per Appendix E section.
// `fields` are shown by default; `more` is the BD-only long tail behind a "+N more" toggle.
function buildOpportunityPreview(candidate, notices, srcName, functions) {
  const { funding, partners } = fundingAndPartners(candidate, notices, srcName);
  const products = [...new Set(candidate.matchedFunctions.map((m) => PRODUCT_MAP[m.function_id]).filter(Boolean))];
  const noModule = candidate.matchedFunctions.some((m) => PRODUCT_MAP[m.function_id] === null);
  const level = candidate.admin === "subnational" ? "Local / Regional" : candidate.admin === "donor" ? "Donor-financed programme" : "Central (Ministry of Finance)";
  const eligOk = candidate.flags.length === 0;
  const dealTier = candidate.score >= 85 ? "Tier 1 — Priority" : "Tier 2 — Developing";
  const pricingNote = `${funding}${candidate.value ? `, est. $${candidate.value.toLocaleString()}` : ""} — Monitor-estimated, unverified.`;

  return [
    { title: "Opportunity information", fields: [
      { label: "Opportunity Name", cat: "D", value: candidate.title },
      { label: "Account Name", cat: "S", value: `Proposed: ${candidate.buyer} — link or create in CRM` },
      { label: "Level of Government", cat: "D", value: level },
      { label: "Funding Source", cat: "D", value: funding },
      { label: "Partners Involved", cat: "D", value: partners.length ? partners.join(", ") : "None detected" },
      { label: "Industry", cat: "D", value: regionOf(candidate.country) },
    ], more: [
      { label: "Stage", cat: "S", value: "TBD, pending picklist (D28)" },
      { label: "Lead Source", cat: "S", value: "Monitor, pending picklist (D28)" },
      { label: "Pipeline", cat: "S", value: "Standard (Sales Opportunity)" },
      { label: "Currency", cat: "S", value: "USD" },
      { label: "Is this a Partner or Reseller-led opportunity?", cat: "S", value: "No" },
      { label: "Closing Date", cat: "B", value: "TBD" },
      { label: "Original Closing Date", cat: "B", value: "TBD" },
      { label: "Sales Forecasting", cat: "B", value: "TBD" },
      { label: "Finance Project ID", cat: "D", value: "blank" },
    ] },
    { title: "Deal classification", fields: [
      { label: "Deal Tier", cat: "S", value: dealTier },
      { label: "Delivery Model", cat: "S", value: partners.length ? "Direct (Partner-Supported)" : "Direct" },
      { label: "Customer Type", cat: "S", value: "New Customer" },
    ], more: [
      { label: "Expected Close Year", cat: "B", value: "TBD" },
    ] },
    { title: "Eligibility requirements", fields: [
      { label: "Eligibility Requirements Met?", cat: "S", value: eligOk ? "Yes" : "Review needed" },
      { label: "Eligibility Requirements Comments", cat: "D", value: eligOk ? "No eligibility barriers detected in the notice text." : `Flags: ${candidate.flags.join(", ")}` },
    ], more: [] },
    { title: "Partner and legal information", fields: [
      { label: "Partner Required?", cat: "S", value: partners.length || candidate.admin === "donor" ? "Yes" : "Unknown" },
      { label: "Partner Required Comments", cat: "D", value: partners.length ? partners.join(", ") : "blank" },
    ], more: [
      { label: "Legal Support Required?", cat: "B", value: "Unknown" },
      { label: "Legal Support Comments", cat: "B", value: "TBD" },
    ] },
    { title: "Submission information", fields: [
      candidate.ptype === "other"
        ? { label: "Expected Release of RFP/EOI?", cat: "S", value: candidate.deadline ?? "TBD" }
        : { label: "Proposal Due or Submitted", cat: "D", value: candidate.deadline ?? "TBD" },
      { label: "Do we need to register our interest?", cat: "S", value: candidate.flags.includes("local_registration") || candidate.flags.includes("consortium_required") ? "Yes" : "Unknown" },
    ], more: [
      { label: "Proposal Documents Received", cat: "B", value: "\u2013" }, { label: "Pre-Bid Meeting", cat: "B", value: "TBD" },
      { label: "Deadline for Questions", cat: "B", value: "TBD" }, { label: "Delivery Logistics and Visa Requirements", cat: "B", value: "TBD" },
      { label: "Number of Copies?", cat: "B", value: "TBD" }, { label: "Is Bid Bond Required", cat: "B", value: "Unknown" },
      { label: "Format of Bid Bond", cat: "B", value: "\u2013" }, { label: "Amount of Bid Bond (if known)", cat: "B", value: "TBD" },
      { label: "Estimated Duration of Contract in Months", cat: "B", value: "TBD" }, { label: "Proposal Valid Until", cat: "B", value: "TBD" },
    ] },
    { title: "Pricing summary", fields: [
      { label: "Total Opportunity Amount", cat: "S", value: candidate.value ? `$${candidate.value.toLocaleString()} (unverified)` : "$0" },
      { label: "Probability (%)", cat: "S", value: "10" },
      { label: "Pricing Notes", cat: "D", value: pricingNote },
    ], more: [
      { label: "Licenses / Implementation / Maintenance / Academy / Sustainability / Third Party (FreeBalance)", cat: "B", value: "\u2013 or $0" },
      { label: "FreeBalance Amount", cat: "B", value: "$0" }, { label: "Tax Amount", cat: "B", value: "\u2013" },
    ] },
    { title: "Products required", fields: [
      { label: "FreeBalance Products Required", cat: "D", value: products.length ? products.join(", ") + (noModule ? " (interface only for HR/payroll or transparency, no dedicated module)" : "") : "No FreeBalance module match yet" },
      { label: "Standard or Custom Product Required?", cat: "S", value: "Standard (COTS)" },
    ], more: [
      { label: "Product Gaps", cat: "B", value: "TBD" }, { label: "Are gaps critical to line of business", cat: "B", value: "\u2013" },
      { label: "Mandatory Requirements", cat: "B", value: "TBD" }, { label: "Evaluation Weighting", cat: "B", value: "TBD" },
      { label: "Do we pass the 80/20 rule?", cat: "B", value: "TBD" }, { label: "Third Party Software", cat: "B", value: "TBD" },
      { label: "Hardware and Systems Software", cat: "B", value: "TBD" }, { label: "Level of Effort (LOE)", cat: "B", value: "TBD" },
    ] },
    { title: "Implementation and pricing", fields: [], more: [
      { label: "Implementation Period", cat: "B", value: "TBD" }, { label: "Number of Users", cat: "B", value: "TBD" },
      { label: "System Go Live", cat: "B", value: "TBD" }, { label: "Available Budget", cat: "B", value: "\u2013" },
      { label: "Warranty Period", cat: "B", value: "TBD" }, { label: "Post-Warranty Period", cat: "B", value: "TBD" }, { label: "Budget Notes", cat: "B", value: "TBD" },
    ] },
  ];
}

// ------------------------------------------------------------------
// UI atoms
// ------------------------------------------------------------------
const clock = () => new Date().toTimeString().slice(0, 5);
const Card = ({ accent, children, className = "", style = {} }) => (
  <div className={`rounded-lg ${className}`} style={{ background: C.card, border: `1px solid ${C.border}`, borderTop: accent ? `3px solid ${accent}` : `1px solid ${C.border}`, ...style }}>{children}</div>
);
const Kpi = ({ icon: Icon, label, value, accent, sub, pct }) => (
  <Card accent={accent} className="p-4">
    <div className="flex items-center gap-4">
      <div className="w-11 h-11 rounded-md flex items-center justify-center shrink-0" style={{ background: accent + "22", color: accent }}><Icon size={20} /></div>
      <div className="min-w-0">
        <div className="text-3xl font-semibold tabular-nums leading-none" style={{ color: accent }}>{value}</div>
        <div className="text-sm mt-1" style={{ color: C.text }}>{label}</div>
      </div>
    </div>
    <div className="mt-3 h-1.5 rounded" style={{ background: C.border }}><div className="h-1.5 rounded" style={{ width: `${Math.max(0, Math.min(100, pct ?? 0))}%`, background: accent }} /></div>
    <div className="mt-1.5 text-xs" style={{ color: C.muted }}>{sub}</div>
  </Card>
);
const Pill = ({ children, color = C.blue }) => (
  <span className="inline-block px-2 py-0.5 rounded text-xs font-medium whitespace-nowrap" style={{ background: color + "22", color, border: `1px solid ${color}55` }}>{children}</span>
);
const Btn = ({ children, onClick, color = C.blue, outline, disabled, icon: Icon, small }) => (
  <button onClick={onClick} disabled={disabled}
    className={`inline-flex items-center gap-2 rounded-md font-medium ${small ? "px-2.5 py-1 text-xs" : "px-3 py-2 text-sm"} disabled:opacity-40 focus:outline-none focus:ring-2`}
    style={outline ? { border: `1px solid ${color}`, color } : { background: color, color: "#0a1020" }}>
    {Icon && <Icon size={small ? 12 : 15} />}{children}
  </button>
);
const Field = ({ label, children, className = "" }) => (
  <label className={`flex flex-col gap-1 ${className}`}><span className="text-xs" style={{ color: C.muted }}>{label}</span>{children}</label>
);
const inputStyle = { background: C.panel, border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: "8px 10px", fontSize: 14 };
const HEALTH = { healthy: C.green, watch: C.amber, failed: C.red, unhealthy: C.red, "never scanned": C.faint };
const CONN = { Feed: C.green, Page: C.blue, Browser: C.purple, Mail: C.cyan };
const Th = ({ cols }) => <thead><tr style={{ color: C.muted, borderBottom: `1px solid ${C.border}` }}>{cols.map((h) => <th key={h} className="text-left font-medium px-3 py-2 whitespace-nowrap">{h}</th>)}</tr></thead>;

// Hero: the one number a leadership readout should lead with.
const Hero = ({ value, accent, caption, sub }) => (
  <div className="flex items-end gap-4 flex-wrap">
    <div className="text-5xl font-semibold tabular-nums leading-none" style={{ color: accent }}>{value}</div>
    <div className="pb-1 min-w-0">
      <div className="text-sm leading-snug" style={{ color: C.text }}>{caption}</div>
      <div className="text-xs mt-1" style={{ color: C.muted }}>{sub}</div>
    </div>
  </div>
);

// Funnel: how much noise the system removes before a human ever sees it, in one glance.
// The count sits in its own fixed-height row above the bar, never inside the
// proportional bar itself, so a small or zero value can never overlap the label below it.
function Funnel({ stages }) {
  const max = Math.max(...stages.map((s) => s.value), 1);
  return (
    <div className="flex items-stretch gap-1.5">
      {stages.map((s, i) => (
        <div key={s.label} className="flex items-stretch gap-1.5 flex-1 min-w-0">
          {i > 0 && <div className="flex items-center shrink-0" style={{ color: C.faint }}><ChevronRight size={14} /></div>}
          <div className="flex-1 min-w-0 flex flex-col items-center">
            <div className="text-lg font-semibold tabular-nums leading-none" style={{ color: s.color }}>{s.value}</div>
            <div className="w-full rounded-md mt-2 relative overflow-hidden" style={{ height: 32, background: C.panel, border: `1px solid ${C.border}` }}>
              <div className="absolute bottom-0 left-0 w-full" style={{ height: `${Math.max(10, (s.value / max) * 100)}%`, background: s.color + "33", borderTop: `2px solid ${s.color}` }} />
            </div>
            <div className="text-xs mt-1.5 text-center leading-tight" style={{ color: C.muted }}>{s.label}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// Field-level trust badges for the Opportunity preview: what the system is sure of, what it
// suggests, and what only a person can judge. Reused in the review queue and the CRM ledger.
const CAT_META = {
  D: { label: "Auto-filled", color: C.green, Icon: Check },
  S: { label: "Suggested, confirm", color: C.amber, Icon: Pencil },
  B: { label: "Yours to complete", color: C.faint, Icon: Minus },
};
const CatBadge = ({ cat }) => {
  const m = CAT_META[cat];
  return <span className="inline-flex items-center gap-1 text-xs whitespace-nowrap" style={{ color: m.color }}><m.Icon size={11} />{m.label}</span>;
};
const FieldRow = ({ f }) => (
  <div className="flex items-start justify-between gap-3 py-1.5" style={{ borderBottom: `1px solid ${C.border}66` }}>
    <div className="text-xs shrink-0" style={{ color: C.muted, width: 170 }}>{f.label}</div>
    <div className="text-sm flex-1 min-w-0" style={{ color: C.text }}>{f.value}</div>
    <div className="w-32 text-right shrink-0"><CatBadge cat={f.cat} /></div>
  </div>
);
function OpportunityPreview({ sections }) {
  const [open, setOpen] = useState({});
  return (
    <div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs pb-2 mb-2" style={{ color: C.muted, borderBottom: `1px solid ${C.border}` }}>
        <span className="inline-flex items-center gap-1"><Check size={11} style={{ color: C.green }} />auto-filled from the notice</span>
        <span className="inline-flex items-center gap-1"><Pencil size={11} style={{ color: C.amber }} />Monitor suggests, you confirm</span>
        <span className="inline-flex items-center gap-1"><Minus size={11} style={{ color: C.faint }} />yours to complete</span>
      </div>
      <div className="space-y-4">
        {sections.map((sec) => (
          <div key={sec.title}>
            <div className="text-sm font-medium mb-0.5">{sec.title}</div>
            {sec.fields.map((f) => <FieldRow key={f.label} f={f} />)}
            {sec.more.length > 0 && (open[sec.title] ? (
              <>
                {sec.more.map((f) => <FieldRow key={f.label} f={f} />)}
                <button onClick={() => setOpen((o) => ({ ...o, [sec.title]: false }))} className="text-xs mt-1.5 flex items-center gap-1" style={{ color: C.blue }}><ChevronDown size={12} />hide {sec.more.length} field{sec.more.length > 1 ? "s" : ""}</button>
              </>
            ) : (
              <button onClick={() => setOpen((o) => ({ ...o, [sec.title]: true }))} className="text-xs mt-1.5 flex items-center gap-1" style={{ color: C.muted }}>
                <ChevronRight size={12} />+{sec.more.length} more field{sec.more.length > 1 ? "s" : ""}{sec.more.every((f) => f.cat === "B") ? ", all yours to complete" : ""}
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// App
// ------------------------------------------------------------------
export default function App() {
  const [tab, setTab] = useState("dashboard");
  const [mode, setMode] = useState("live");
  const [threshold, setThreshold] = useState(60);
  const [reviewer, setReviewer] = useState("");
  const [functions, setFunctions] = useState(FUNCTIONS_INITIAL);
  const [sources, setSources] = useState(SOURCES_INITIAL.map((s) => ({ ...s, lastScan: null, health: "never scanned", failures: 0, zeroYield: 0, outage: false })));
  const [db, setDb] = useState({ notices: [], scores: [], candidates: [], leads: [], events: [], seen: {}, log: [], runs: 0, calls: 0, translations: 0, tokIn: 0, tokOut: 0 });
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState(null);
  const [selected, setSelected] = useState(null);
  const [srcFilter, setSrcFilter] = useState("all");
  const [coverageOpen, setCoverageOpen] = useState(false);
  const runRef = useRef(0);

  const ev = (entity, action, actor, before, after) => ({ t: clock(), entity, action, actor, before, after });
  const say = (m) => setToast(m);
  const nextId = (prefix, arr) => `${prefix}${String(arr.length + 1).padStart(3, "0")}`;
  const srcName = (id) => sources.find((s) => s.id === id)?.name ?? id;

  async function runScans(ids) {
    if (running) return;
    setRunning(true);
    const run = db.runs + 1;
    runRef.current = run;
    setDb((d) => ({ ...d, log: [...d.log, `${clock()}  run ${run} started (${ids.length} sources, scoring: ${mode === "live" ? "Claude, live" : "mock, deterministic"}, threshold ${threshold})`] }));
    for (const sid of ids) {
      const src = sources.find((s) => s.id === sid);
      if (!src.enabled) continue;
      // Acquire. A failing connector raises and marks the source unhealthy. No second attempt in code.
      if (src.outage) {
        setSources((ss) => ss.map((s) => s.id === sid ? { ...s, outage: false, failures: s.failures + 1, lastScan: clock(), health: s.failures + 1 >= 3 ? "unhealthy" : "failed" } : s));
        setDb((d) => ({ ...d, log: [...d.log, `${clock()}  ${src.name}: FetchError: connection refused (consecutive failures ${src.failures + 1}${src.failures + 1 >= 3 ? ", alarm raised" : ""})`], events: [...d.events, ev(`source ${sid}`, "fetch_failed", "scan run", "healthy", "failed")] }));
        continue;
      }
      const available = FIXTURES.filter((n) => n.source === sid && n.wave <= run);
      const fresh = available.filter((n) => !db.seen[hashOf(n.title + n.body)]);
      const seenNow = {}; fresh.forEach((n) => { seenNow[hashOf(n.title + n.body)] = n.id; });
      if (fresh.length === 0) {
        const zy = src.zeroYield + 1;
        const anomaly = src.expected[0] > 0 && zy >= 2;
        setSources((ss) => ss.map((s) => s.id === sid ? { ...s, lastScan: clock(), zeroYield: zy, failures: 0, health: anomaly ? "watch" : "healthy" } : s));
        setDb((d) => ({ ...d, log: [...d.log, `${clock()}  ${src.name}: ${available.length} seen, 0 new${anomaly ? `  (zero-yield anomaly, ${zy} consecutive runs)` : ""}`] }));
        continue;
      }
      // Filter, translate where the lexicon cannot read the language, then score survivors.
      const results = [];
      for (const n0 of fresh) {
        let n = n0, tr = null, f = stageFilter(n0, functions);
        if (f.pass === null) {
          try { tr = mode === "mock" ? mockTranslate(n0) : await claudeTranslate(n0); }
          catch (e) { results.push({ n, filter: f, error: `translation: ${e.message}` }); continue; }
          n = { ...n0, title_en: tr.title_en, body_en: tr.body_en };
          f = stageFilter(n, functions);
          f.reason = `translated (${n0.lang}, ${tr.model}); ${f.reason}`;
        }
        if (!f.pass) { results.push({ n, filter: f, tr }); continue; }
        let score = null, error = null;
        if (mode === "mock") score = mockScore(n, functions, f.hits);
        else {
          for (let attempt = 1; attempt <= 2 && !score; attempt++) {
            try { score = await claudeScore(n, functions); error = null; }
            catch (e) { error = e.message; }
          }
        }
        results.push({ n, filter: f, tr, score, error });
      }
      // Dedupe and stage against the current state, in one update.
      setDb((d) => {
        let notices = [...d.notices], scores = [...d.scores], candidates = [...d.candidates], events = [...d.events], log = [...d.log];
        let calls = d.calls, translations = d.translations, tokIn = d.tokIn, tokOut = d.tokOut, dupes = 0, staged = 0, filtered = 0, parked = 0;
        for (const r of results) {
          const notice = { ...r.n, hash: hashOf(r.n.title + r.n.body), filterResult: r.filter.reason, status: r.filter.pass ? "scored" : "filtered_out" };
          notices.push(notice);
          if (r.tr && r.tr.model !== "mock") { translations++; tokIn += r.tr.tokens_in; tokOut += r.tr.tokens_out; }
          if (r.error && !r.score) { parked++; notice.status = "parked"; log.push(`${clock()}  ${r.n.externalId}: parked: ${r.error}`); events.push(ev(`notice ${r.n.id}`, "parked", "scan run", "detected", r.error)); continue; }
          if (!r.filter.pass) { filtered++; continue; }
          const s = r.score; scores.push({ noticeId: r.n.id, ...s });
          if (s.model !== "mock") { calls++; tokIn += s.tokens_in; tokOut += s.tokens_out; }
          const dup = findDuplicate(notice, s, candidates);
          if (dup) {
            dupes++;
            candidates = candidates.map((c) => c.id === dup.c.id ? { ...c, noticeIds: [...c.noticeIds, r.n.id], matches: [...c.matches, { noticeId: r.n.id, method: dup.method, score: dup.score }], score: Math.max(c.score, s.relevance), value: c.value ?? s.estimated_value_usd } : c);
            events.push(ev(`candidate ${dup.c.id}`, "duplicate_joined", "scan run", `${dup.c.noticeIds.length} sources`, `${dup.c.noticeIds.length + 1} sources (${dup.method})`));
            continue;
          }
          const id = nextId("C", candidates);
          const above = s.relevance >= threshold;
          candidates.push({ id, primaryNoticeId: r.n.id, noticeIds: [r.n.id], matches: [], score: s.relevance, status: above ? "pending_review" : "below_threshold",
            title: s.title_en, originalTitle: r.n.title, lang: r.n.lang, buyer: r.n.buyer, country: r.n.country, admin: r.n.admin, summary: s.summary_en, matchedFunctions: s.matched_functions, systemNames: s.system_names,
            ptype: s.procurement_type, value: s.estimated_value_usd, flags: s.eligibility_flags, deadline: s.deadline_at, confidence: s.confidence, model: s.model, detectedRun: run });
          events.push(ev(`candidate ${id}`, "scored", "scan run", "detected", `${s.relevance} (${s.model})`));
          if (above) { staged++; events.push(ev(`candidate ${id}`, "staged", "scan run", "scored", "pending_review")); }
        }
        const nTr = results.filter((r) => r.tr).length;
        log.push(`${clock()}  ${src.name}: ${results.length} new, ${nTr ? `${nTr} translated, ` : ""}${filtered} filtered out, ${results.length - filtered - parked} scored, ${dupes} duplicates joined, ${staged} moved to pending_review${parked ? `, ${parked} parked` : ""}`);
        return { ...d, notices, scores, candidates, events, log, calls, translations, tokIn, tokOut, seen: { ...d.seen, ...seenNow } };
      });
      setSources((ss) => ss.map((s) => s.id === sid ? { ...s, lastScan: clock(), zeroYield: 0, failures: 0, health: "healthy" } : s));
    }
    setDb((d) => ({ ...d, runs: run, log: [...d.log, `${clock()}  run ${run} finished`] }));
    setRunning(false);
  }

  function decide(c, action, edits, reason) {
    if (!reviewer.trim()) { say("Reviewer name is required before any decision."); return; }
    if (action === "reject" && !reason) { say("A rejection reason is required."); return; }
    const who = reviewer.trim();
    setDb((d) => {
      let candidates = [...d.candidates], leads = [...d.leads], events = [...d.events];
      const before = candidates.find((x) => x.id === c.id);
      if (action === "reject") {
        candidates = candidates.map((x) => x.id === c.id ? { ...x, status: "rejected", reviewer: who, rejectionReason: reason } : x);
        events.push(ev(`candidate ${c.id}`, "rejected", who, "pending_review", `rejected: ${reason}`));
        return { ...d, candidates, events };
      }
      const merged = { ...before, ...edits };
      if (edits && (edits.title !== before.title || edits.summary !== before.summary || edits.value !== before.value)) events.push(ev(`candidate ${c.id}`, "edited", who, before.title, merged.title));
      const leadId = nextId("L", leads);
      const payload = { Lead_Source: "Monitor", Company: merged.buyer, Lead_Title: merged.title, Country: merged.country, Original_Language: merged.lang, Candidate_Id: c.id, Monitor_Score: merged.score, Estimated_Value_USD: merged.value, Deadline: merged.deadline, Description: merged.summary, Source_Links: merged.noticeIds.map((nid) => d.notices.find((n) => n.id === nid)?.url), Approved_By: who };
      leads.push({ id: leadId, candidateId: c.id, payload, approvedBy: who, at: clock() });
      candidates = candidates.map((x) => x.id === c.id ? { ...merged, status: "notified", reviewer: who, crmLeadId: leadId } : x);
      events.push(ev(`candidate ${c.id}`, "approved", who, "pending_review", "approved"));
      events.push(ev(`crm_lead ${leadId}`, "created", `system (post-approval, as ${who})`, "(none)", "payload"));
      events.push(ev(`candidate ${c.id}`, "notified", "system (post-approval)", "lead_created", `notified: ${WA_ISO.includes(merged.country) ? "West Africa" : "Europe"} regional lead, Cliq`));
      return { ...d, candidates, leads, events };
    });
    setSelected(null);
    say(action === "reject" ? `Rejected ${c.id}. Reason recorded for rubric tuning.` : `Approved ${c.id}. CRM lead created and regional lead notified.`);
  }

  const pending = db.candidates.filter((c) => c.status === "pending_review").sort((a, b) => b.score - a.score);
  const approved = db.candidates.filter((c) => ["approved", "lead_created", "notified"].includes(c.status));
  const rejected = db.candidates.filter((c) => c.status === "rejected");
  const reviewed = approved.length + rejected.length;
  const precision = reviewed ? Math.round((approved.length / reviewed) * 100) : null;
  const cost = (db.tokIn * 1 + db.tokOut * 5) / 1e6;
  const detected = db.candidates.length;
  const langs = new Set(db.notices.map((n) => n.lang));

  // Country coverage: which enabled sources reach each of the 47 countries, and whether any has scanned.
  const coverage = COUNTRIES.map((c) => {
    const reach = sources.filter((s) => s.stream !== "C" && (s.country === c.iso || (s.covers ?? []).includes(c.iso)));
    const enabled = reach.filter((s) => s.enabled);
    const scanned = enabled.some((s) => s.lastScan);
    const own = reach.some((s) => s.country === c.iso);
    const status = scanned ? "scanned" : enabled.length ? "enabled" : reach.length ? "planned" : "gap";
    return { ...c, reach, own, status, wave: Math.min(...reach.map((s) => s.wave), 9) };
  });
  const COV = { scanned: C.green, enabled: C.blue, planned: C.amber, gap: C.red };
  const coverageCounts = { scanned: coverage.filter((c) => c.status === "scanned").length, enabled: coverage.filter((c) => c.status === "enabled").length, planned: coverage.filter((c) => c.status === "planned").length, gap: coverage.filter((c) => c.status === "gap").length };
  const unhealthySources = sources.filter((s) => s.enabled && ["failed", "unhealthy", "watch"].includes(s.health));
  const survivors = db.notices.filter((n) => n.status !== "filtered_out").length;
  const everStaged = db.candidates.filter((c) => c.status !== "below_threshold").length;
  const pipelineValue = approved.reduce((s, c) => s + (c.value || 0), 0);
  const approvedCountries = new Set(approved.map((c) => c.country)).size;

  const TABS = [["dashboard", "Dashboard", LayoutDashboard], ["sources", "Sources", Database], ["queue", "Review queue", Inbox], ["crm", "CRM ledger", ClipboardCheck], ["map", "Component map", Map], ["audit", "Audit log", ScrollText]];
  const shownSources = sources.filter((s) => srcFilter === "all" || (srcFilter === "wa" && (WA_ISO.includes(s.country))) || (srcFilter === "eu" && s.country !== "multi" && !WA_ISO.includes(s.country)) || (srcFilter === "global" && s.country === "multi"));

  return (
    <div className="min-h-screen" style={{ background: C.bg, color: C.text, fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif" }}>
      <div className="flex items-center gap-4 px-5 py-3 flex-wrap" style={{ background: C.panel, borderBottom: `1px solid ${C.border}` }}>
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-md flex items-center justify-center" style={{ background: C.blue + "22", color: C.blue }}><ShieldCheck size={20} /></div>
          <div><div className="font-semibold leading-tight">PFM Opportunity Monitor</div><div className="text-xs" style={{ color: C.muted }}>EMEA pilot, 47 countries, simulation for discussion</div></div>
        </div>
        <nav className="flex gap-1 flex-wrap">
          {TABS.map(([id, label, Icon]) => {
            const n = id === "queue" ? pending.length : id === "crm" ? db.leads.length : 0;
            return (
              <button key={id} onClick={() => setTab(id)} className="flex items-center gap-2 px-4 py-2 rounded-md text-sm focus:outline-none focus:ring-2"
                style={tab === id ? { background: C.blue + "33", color: C.text, border: `1px solid ${C.blue}` } : { color: C.muted, border: "1px solid transparent" }}>
                <Icon size={14} />{label}{n > 0 && <span className="px-1.5 rounded text-xs" style={{ background: id === "queue" ? C.orange : C.green, color: "#0a1020" }}>{n}</span>}
              </button>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          <Pill color={mode === "live" ? C.purple : C.cyan}>{mode === "live" ? "Claude, live" : "Mock scoring"}</Pill>
          <span style={{ color: C.muted }}>Ryan Dear</span>
          <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold" style={{ background: C.blue, color: "#0a1020" }}>RD</div>
        </div>
      </div>

      <div className="flex items-end gap-3 px-5 py-3 flex-wrap" style={{ borderBottom: `1px solid ${C.border}` }}>
        <Field label="Reviewer (required for any decision)"><input style={{ ...inputStyle, width: 200, borderColor: reviewer ? C.border : C.orange }} placeholder="e.g. Matthew" value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></Field>
        <Field label="Scoring and translation"><select style={inputStyle} value={mode} onChange={(e) => setMode(e.target.value)}><option value="live">Claude, live calls on fixture notices</option><option value="mock">Mock, deterministic, offline</option></select></Field>
        <Field label="Stage threshold"><input type="number" min={0} max={100} style={{ ...inputStyle, width: 80 }} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /></Field>
        <Field label="Scope"><div style={{ ...inputStyle, color: C.muted, whiteSpace: "nowrap" }}>47 countries · en/fr lexicons, all else auto-translated</div></Field>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs" style={{ color: C.muted }}>{db.runs ? `run ${db.runs}, last scan ${sources.filter((s) => s.lastScan).map((s) => s.lastScan).sort().pop()}` : "no scans yet"}</span>
          <Btn icon={running ? RefreshCw : Play} onClick={() => runScans(sources.map((s) => s.id))} disabled={running}>{running ? "Scanning" : "Run all scans"}</Btn>
        </div>
      </div>

      {toast && (
        <div className="mx-5 mt-3 px-4 py-2 rounded-md text-sm flex items-center gap-3" style={{ background: C.card, border: `1px solid ${C.border}` }}>
          <Bell size={14} style={{ color: C.blue }} /><span>{toast}</span><button className="ml-auto" onClick={() => setToast(null)} style={{ color: C.muted }}><X size={14} /></button>
        </div>
      )}

      <main className="px-5 py-5">
        {tab === "dashboard" && (
          <div>
            <div className="flex items-baseline justify-between flex-wrap gap-2">
              <h2 className="text-xl font-semibold">Leadership readout</h2>
              <div className="text-xs" style={{ color: C.muted }}>{db.runs ? `run ${db.runs} · last scan ${sources.filter((s) => s.lastScan).map((s) => s.lastScan).sort().pop()}` : "no scans yet"}</div>
            </div>

            <Card className="p-5 mt-4">
              <Hero
                accent={pipelineValue > 0 ? C.green : C.muted}
                value={pipelineValue > 0 ? `$${(pipelineValue / 1e6).toFixed(1)}M` : approved.length > 0 ? String(approved.length) : "—"}
                caption={pipelineValue > 0
                  ? `pipeline value tagged Source: Monitor, from ${approved.length} approved lead${approved.length === 1 ? "" : "s"} across ${approvedCountries} countr${approvedCountries === 1 ? "y" : "ies"}`
                  : "no leads approved yet — this fills in as the reviewer works the queue"}
                sub={`the number that matters to leadership, per the pilot plan · ${db.notices.length} notices seen this session, ${langs.size} language${langs.size === 1 ? "" : "s"}`}
              />
              <div className="mt-5">
                <Funnel stages={[
                  { label: "Notices seen", value: db.notices.length, color: C.blue },
                  { label: "Passed the filter", value: survivors, color: C.cyan },
                  { label: "Staged for review", value: everStaged, color: C.amber },
                  { label: "Reviewed", value: reviewed, color: C.purple },
                  { label: "Approved", value: approved.length, color: C.green },
                ]} />
              </div>
            </Card>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4">
              <Kpi icon={Inbox} label="Awaiting review" value={pending.length} accent={C.orange} pct={detected ? (pending.length / detected) * 100 : 0} sub={`~${pending.length * 3} reviewer minutes`} />
              <Kpi icon={ClipboardCheck} label="Precision at the queue" value={precision === null ? "–" : `${precision}%`} accent={C.purple} pct={precision ?? 0} sub="gate: 40% by week 12" />
              <Kpi icon={Languages} label="Translated at ingestion" value={db.notices.filter((n) => n.lang !== "en").length} accent={C.cyan} pct={db.notices.length ? (db.notices.filter((n) => n.lang !== "en").length / db.notices.length) * 100 : 0} sub="original text retained" />
              <Kpi icon={Cpu} label="Model calls today" value={db.calls + db.translations} accent={C.amber} pct={Math.min(100, ((db.calls + db.translations) / 600) * 100)} sub={`USD ${cost.toFixed(2)} · cap 600/day`} />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-6">
              <Card className="p-4">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium">Country coverage</div>
                  <button onClick={() => setCoverageOpen((o) => !o)} className="text-xs flex items-center gap-1" style={{ color: C.blue }}>{coverageOpen ? "Hide detail" : "Show all 47"}{coverageOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>
                </div>
                <div className="flex h-2.5 rounded overflow-hidden mt-3" style={{ background: C.border }}>
                  {["scanned", "enabled", "planned", "gap"].map((k) => coverageCounts[k] > 0 && <div key={k} style={{ width: `${(coverageCounts[k] / 47) * 100}%`, background: COV[k] }} />)}
                </div>
                <div className="flex gap-4 mt-2 text-xs flex-wrap">
                  {["scanned", "enabled", "planned", "gap"].map((k) => <span key={k} className="inline-flex items-center gap-1.5" style={{ color: C.muted }}><span className="w-2 h-2 rounded-full" style={{ background: COV[k] }} />{coverageCounts[k]} {k}</span>)}
                </div>
                {coverageOpen && ["West Africa", "Europe"].map((region) => (
                  <div key={region} className="mt-3">
                    <div className="text-xs font-medium mb-1" style={{ color: C.muted }}>{region}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {coverage.filter((c) => c.region === region).map((c) => (
                        <span key={c.iso} title={`${c.name}: ${c.reach.map((s) => s.name).join("; ") || "no source"}`} className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs" style={{ background: COV[c.status] + "1a", border: `1px solid ${COV[c.status]}66`, color: C.text }}>
                          <span className="w-1.5 h-1.5 rounded-full" style={{ background: COV[c.status] }} />{c.name}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </Card>

              <Card className="p-4">
                <div className="text-sm font-medium mb-3">Source health</div>
                {unhealthySources.length === 0 ? (
                  <div className="text-sm flex items-center gap-2" style={{ color: C.green }}><Check size={16} />All {sources.filter((s) => s.enabled).length} enabled sources healthy</div>
                ) : (
                  <div className="space-y-2">
                    {unhealthySources.map((s) => (
                      <div key={s.id} className="flex items-center justify-between text-sm">
                        <span className="truncate pr-2">{s.name}</span>
                        <Pill color={HEALTH[s.health]}>{s.health}{s.failures ? ` (${s.failures})` : ""}</Pill>
                      </div>
                    ))}
                  </div>
                )}
                <div className="text-xs mt-3" style={{ color: C.faint }}>Full detail on the Sources tab.</div>
              </Card>
            </div>

            {rejected.length > 0 && (
              <div className="mt-6">
                <div className="text-sm font-medium mb-2">Rejection reasons</div>
                <Card className="p-4">{Object.entries(rejected.reduce((a, c) => ({ ...a, [c.rejectionReason]: (a[c.rejectionReason] || 0) + 1 }), {})).map(([r, n]) => (
                  <div key={r} className="flex items-center gap-3 text-sm py-1"><span className="w-56">{r}</span><div className="flex-1 h-2 rounded" style={{ background: C.border }}><div className="h-2 rounded" style={{ width: `${(n / rejected.length) * 100}%`, background: C.red }} /></div><span className="w-6 text-right tabular-nums">{n}</span></div>
                ))}</Card>
              </div>
            )}
          </div>
        )}

        {tab === "sources" && (
          <div>
            <div className="flex items-end justify-between mb-4 gap-3 flex-wrap">
              <div><h2 className="text-xl font-semibold">Sources</h2><div className="text-xs" style={{ color: C.muted }}>{sources.length} registry entries in the simulation; the full inventory (98 rows, 47 countries) is the companion spreadsheet. Feeds first; a custom connector only where nothing else covers a portal.</div></div>
              <div className="flex gap-1">{[["all", "All"], ["global", "Donors and regional"], ["eu", "Europe"], ["wa", "West Africa"]].map(([k, l]) => <button key={k} onClick={() => setSrcFilter(k)} className="px-3 py-1.5 rounded-md text-xs" style={srcFilter === k ? { background: C.blue + "33", border: `1px solid ${C.blue}` } : { color: C.muted, border: `1px solid ${C.border}` }}>{l}</button>)}</div>
            </div>
            {unhealthySources.length > 0 ? (
              <Card accent={C.amber} className="p-3 mb-4">
                <div className="text-sm font-medium mb-2" style={{ color: C.amber }}>{unhealthySources.length} source{unhealthySources.length > 1 ? "s" : ""} need attention</div>
                <div className="flex flex-wrap gap-2">{unhealthySources.map((s) => <Pill key={s.id} color={HEALTH[s.health]}>{s.name}: {s.health}</Pill>)}</div>
              </Card>
            ) : (
              <Card accent={C.green} className="p-3 mb-4 text-sm flex items-center gap-2" style={{ color: C.green }}><Check size={15} />All {sources.filter((s) => s.enabled).length} enabled sources healthy</Card>
            )}
            <Card className="overflow-x-auto">
              <table className="w-full text-sm">
                <Th cols={["Source", "Country", "Lang", "Stream", "Connector", "Access", "Wave", "Expected", "Last scan", "Health", "On", ""]} />
                <tbody>{shownSources.map((s) => (
                  <tr key={s.id} style={{ borderBottom: `1px solid ${C.border}`, opacity: s.enabled ? 1 : 0.55 }}>
                    <td className="px-3 py-2 font-medium">{s.name}{s.covers && <div className="text-xs font-normal" style={{ color: C.faint }}>reaches {s.covers.length} of the 47 countries</div>}</td>
                    <td className="px-3 py-2">{s.country}</td><td className="px-3 py-2" style={{ color: C.muted }}>{s.lang}</td><td className="px-3 py-2">{s.stream}</td>
                    <td className="px-3 py-2"><Pill color={CONN[s.connector]}>{s.connector}</Pill></td>
                    <td className="px-3 py-2" style={{ color: C.muted }}>{s.access}</td><td className="px-3 py-2 tabular-nums">{s.wave}</td><td className="px-3 py-2 tabular-nums" style={{ color: C.muted }}>{s.expected[0]} to {s.expected[1]}</td>
                    <td className="px-3 py-2 tabular-nums">{s.lastScan ?? "–"}</td>
                    <td className="px-3 py-2"><Pill color={HEALTH[s.health]}>{s.health}{s.failures ? ` (${s.failures})` : ""}</Pill></td>
                    <td className="px-3 py-2"><input type="checkbox" checked={s.enabled} onChange={() => setSources((ss) => ss.map((x) => x.id === s.id ? { ...x, enabled: !x.enabled } : x))} /></td>
                    <td className="px-3 py-2 whitespace-nowrap"><span className="inline-flex gap-2">
                      <Btn small outline icon={Play} onClick={() => runScans([s.id])} disabled={running || !s.enabled}>Run scan</Btn>
                      <Btn small outline color={s.outage ? C.red : C.muted} icon={AlertTriangle} onClick={() => setSources((ss) => ss.map((x) => x.id === s.id ? { ...x, outage: !x.outage } : x))}>{s.outage ? "Outage armed" : "Simulate outage"}</Btn>
                    </span></td>
                  </tr>
                ))}</tbody>
              </table>
            </Card>
            <h3 className="text-lg font-semibold mt-6 mb-2">Run log</h3>
            <Card className="p-3 font-mono text-xs" style={{ color: C.muted, minHeight: 120, maxHeight: 320, overflowY: "auto" }}>
              {db.log.length === 0 ? <div>No runs yet. Run all scans, or run one source. A second run shows change detection: nothing already seen is processed again.</div> : db.log.map((l, i) => <div key={i} style={{ color: /FetchError|parked|anomaly/.test(l) ? C.amber : C.muted }}>{l}</div>)}
            </Card>
            <div className="text-xs mt-2" style={{ color: C.faint }}>Simulation notes: sources read from recorded fixtures instead of the live portals. Notices in a language without a lexicon (anything other than English or French) and without a CPV code are translated before the free filter; everything that passes the filter is scored in one model call that also returns the English title. Live mode retries a schema failure once, then parks the notice. The pilot's borderline re-score step is not simulated.</div>
          </div>
        )}

        {tab === "queue" && (
          <div>
            <div className="flex items-center gap-3 mb-4 flex-wrap">
              <h2 className="text-xl font-semibold">Review queue</h2>
              <Pill color={C.orange}>Human checkpoint: nothing reaches the CRM without a named approval here</Pill>
            </div>
            {pending.length === 0 ? (
              <Card className="p-8 text-center" style={{ color: C.muted }}>{db.runs ? "Queue is empty. Every candidate above threshold has been reviewed." : "Queue is empty. Run all scans to fill it."}</Card>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
                <Card className="lg:col-span-2">
                  <div className="px-3 py-2 text-xs" style={{ color: C.muted }}>Pending review ({pending.length}), sorted by score</div>
                  {pending.map((c) => {
                    const days = daysUntil(c.deadline);
                    const urgency = days === null ? { label: "no deadline", color: C.faint }
                      : days < 0 ? { label: "closed", color: C.faint }
                      : days <= 3 ? { label: `due in ${days}d`, color: C.red }
                      : days <= 9 ? { label: `due in ${days}d`, color: C.amber }
                      : { label: `due ${c.deadline}`, color: C.muted };
                    const region = WA_ISO.includes(c.country) ? "West Africa" : "Europe";
                    return (
                      <button key={c.id} onClick={() => setSelected({ id: c.id, editing: false, edits: null, reason: "" })} className="w-full text-left px-3 py-3 focus:outline-none focus:ring-2" style={{ background: selected?.id === c.id ? C.blue + "22" : "transparent", borderTop: `1px solid ${C.border}` }}>
                        <div className="flex items-start gap-3">
                          <div className="text-2xl font-semibold tabular-nums w-10 shrink-0" style={{ color: c.score >= 80 ? C.green : c.score >= 60 ? C.amber : C.muted }}>{c.score}</div>
                          <div className="min-w-0 flex-1">
                            <div className="text-sm font-medium leading-snug">{c.title}</div>
                            <div className="flex flex-wrap gap-1.5 mt-1.5 items-center">
                              <Pill color={region === "West Africa" ? C.orange : C.blue}>{c.country}</Pill>
                              {c.lang !== "en" && <Pill color={C.cyan}>{c.lang}</Pill>}
                              {c.admin === "subnational" && <Pill color={C.purple}>sub-national</Pill>}
                              <span className="text-xs" style={{ color: urgency.color }}>{urgency.label}</span>
                              <span className="text-xs" style={{ color: C.faint }}>· {c.noticeIds.length} source{c.noticeIds.length > 1 ? "s" : ""}</span>
                            </div>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </Card>
                <div className="lg:col-span-3">
                  {selected && pending.find((c) => c.id === selected.id) ? <Detail c={pending.find((c) => c.id === selected.id)} sel={selected} setSel={setSelected} db={db} srcName={srcName} functions={functions} decide={decide} reviewer={reviewer} /> : <Card className="p-8 text-center" style={{ color: C.muted }}>Select a candidate to review it.</Card>}
                </div>
              </div>
            )}
            <div className="text-xs mt-4" style={{ color: C.faint }}>Candidates below threshold ({db.candidates.filter((c) => c.status === "below_threshold").length}) stay in the pipeline database for tuning and are never shown to the reviewer.</div>
          </div>
        )}

        {tab === "crm" && (
          <div>
            <h2 className="text-xl font-semibold">CRM ledger</h2>
            <div className="text-xs mb-4" style={{ color: C.muted }}>Read-only stand-in for the Zoho CRM lead stage. Nothing here calls a real CRM. Every row was created by an approval, under the reviewer's identity.</div>
            {db.leads.length === 0 ? <Card className="p-8 text-center" style={{ color: C.muted }}>No leads yet. Approve a candidate in the review queue.</Card> : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <Card className="overflow-x-auto"><table className="w-full text-sm">
                  <Th cols={["Lead", "Title", "Country", "Approved by", "Tag", "Created"]} />
                  <tbody>{db.leads.map((l) => (
                    <tr key={l.id} style={{ borderBottom: `1px solid ${C.border}` }} className="cursor-pointer" onClick={() => setSelected({ lead: l.id })}>
                      <td className="px-3 py-2 tabular-nums">{l.id}</td><td className="px-3 py-2">{l.payload.Lead_Title}</td><td className="px-3 py-2">{l.payload.Country}</td><td className="px-3 py-2">{l.approvedBy}</td><td className="px-3 py-2"><Pill color={C.green}>Source: Monitor</Pill></td><td className="px-3 py-2 tabular-nums">{l.at}</td>
                    </tr>))}</tbody></table></Card>
                <Card className="p-3">
                  <div className="text-xs mb-2" style={{ color: C.muted }}>Opportunity preview{selected?.lead ? ` — ${selected.lead}` : " — most recent lead"}, per Architecture v0.3 appendix E</div>
                  {(() => {
                    const lead = db.leads.find((l) => l.id === selected?.lead) ?? db.leads[db.leads.length - 1];
                    const cand = db.candidates.find((x) => x.id === lead.candidateId);
                    return cand ? <OpportunityPreview sections={buildOpportunityPreview(cand, db.notices, srcName, functions)} /> : <div style={{ color: C.muted }}>Candidate no longer available.</div>;
                  })()}
                </Card>
              </div>
            )}
          </div>
        )}

        {tab === "map" && (
          <div>
            <h2 className="text-xl font-semibold">Component map and signal lexicon</h2>
            <div className="text-xs mb-4" style={{ color: C.muted }}>Scoring runs at the function level. The free filter reads English and French; every other language is translated to English first and filtered with the English column. Edits apply on the next scan. Weight comes from the map's Type column.</div>
            <Card className="overflow-x-auto"><table className="w-full text-sm">
              <Th cols={["Function", "Pillar", "Type", "Weight", "English keywords", "French keywords"]} />
              <tbody>{functions.map((f) => (
                <tr key={f.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td className="px-3 py-2 font-medium whitespace-nowrap">{f.name}</td><td className="px-3 py-2" style={{ color: C.muted }}>{f.pillar}</td><td className="px-3 py-2 whitespace-nowrap" style={{ color: C.muted }}>{f.type}</td>
                  <td className="px-3 py-2"><input type="number" step="0.1" min="0" max="3" style={{ ...inputStyle, width: 64, padding: "4px 6px" }} value={f.weight} onChange={(e) => setFunctions((fs) => fs.map((x) => x.id === f.id ? { ...x, weight: Number(e.target.value) } : x))} /></td>
                  <td className="px-3 py-2"><input style={{ ...inputStyle, width: "100%", minWidth: 260, padding: "4px 6px" }} value={f.en} onChange={(e) => setFunctions((fs) => fs.map((x) => x.id === f.id ? { ...x, en: e.target.value } : x))} /></td>
                  <td className="px-3 py-2"><input style={{ ...inputStyle, width: "100%", minWidth: 260, padding: "4px 6px" }} value={f.fr} onChange={(e) => setFunctions((fs) => fs.map((x) => x.id === f.id ? { ...x, fr: e.target.value } : x))} /></td>
                </tr>))}</tbody></table></Card>
            <div className="text-xs mt-3" style={{ color: C.muted }}>System names that add signal: {SYSTEM_NAMES.join(", ")}. Geography weights: West Africa 1.0; Ukraine and the Western Balkans 0.8; EU, EEA, UK and Switzerland 0.6. In the pilot these live in versioned config files with a change log; in this simulation, edits apply immediately.</div>
          </div>
        )}

        {tab === "audit" && (
          <div>
            <h2 className="text-xl font-semibold">Audit log</h2>
            <div className="text-xs mb-4" style={{ color: C.muted }}>Every transition, attributed. Pipeline actions are the scan run; review actions are the named reviewer; CRM writes are the system acting after, and only after, an approval.</div>
            {db.events.length === 0 ? <Card className="p-8 text-center" style={{ color: C.muted }}>No events yet.</Card> : (
              <Card className="overflow-x-auto"><table className="w-full text-sm">
                <Th cols={["Time", "Entity", "Action", "Actor", "Before", "After"]} />
                <tbody>{[...db.events].reverse().map((e, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                    <td className="px-3 py-2 tabular-nums" style={{ color: C.muted }}>{e.t}</td><td className="px-3 py-2 font-mono text-xs">{e.entity}</td>
                    <td className="px-3 py-2"><Pill color={{ approved: C.green, created: C.green, notified: C.green, rejected: C.red, parked: C.red, fetch_failed: C.red, edited: C.orange, staged: C.amber, scored: C.blue, duplicate_joined: C.cyan }[e.action] ?? C.muted}>{e.action}</Pill></td>
                    <td className="px-3 py-2">{e.actor}</td><td className="px-3 py-2 text-xs" style={{ color: C.muted }}>{e.before}</td><td className="px-3 py-2 text-xs">{e.after}</td>
                  </tr>))}</tbody></table></Card>
            )}
          </div>
        )}
      </main>
      <div className="px-5 py-3 text-xs" style={{ color: C.faint, borderTop: `1px solid ${C.border}` }}>Simulation, draft v0.3, 11 September 2026. Recorded fixtures in nine languages stand in for the portals; the mock CRM stands in for Zoho, and the Opportunity preview mirrors Architecture v0.3 appendix E. The one property that carries over unchanged: the pipeline cannot create a lead, only an approval can.</div>
    </div>
  );
}

// ------------------------------------------------------------------
// Candidate detail with the checkpoint controls
// ------------------------------------------------------------------
const REASONS = ["Not PFM", "Wrong geography", "Below value floor", "Eligibility barrier", "Duplicate of existing deal", "Translation wrong", "Other"];
function Detail({ c, sel, setSel, db, srcName, functions, decide, reviewer }) {
  const e = sel.edits ?? { title: c.title, summary: c.summary, value: c.value ?? "" };
  const setE = (k, v) => setSel((s) => ({ ...s, editing: true, edits: { ...e, [k]: v } }));
  const linked = c.noticeIds.map((id) => db.notices.find((n) => n.id === id)).filter(Boolean);
  const primary = linked.find((n) => n.id === c.primaryNoticeId);
  const edits = { title: e.title, summary: e.summary, value: e.value === "" ? null : Number(e.value) };
  const preview = buildOpportunityPreview({ ...c, title: e.title, value: e.value === "" ? c.value : Number(e.value) }, db.notices, srcName, functions);
  return (
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <div className="text-4xl font-semibold tabular-nums" style={{ color: c.score >= 80 ? C.green : C.amber }}>{c.score}</div>
        <div className="flex-1 min-w-0">
          {sel.editing ? <input style={{ ...inputStyle, width: "100%" }} value={e.title} onChange={(x) => setE("title", x.target.value)} /> : <div className="text-lg font-semibold leading-snug">{c.title}</div>}
          <div className="text-xs mt-1 flex gap-2 flex-wrap items-center" style={{ color: C.muted }}><span>{c.buyer}, {c.country}. Scored by {c.model}. Confidence {c.confidence}. Type: {c.ptype}.</span>{c.lang !== "en" && <Pill color={C.cyan}>original in {c.lang}, translated at ingestion</Pill>}</div>
        </div>
      </div>
      {c.lang !== "en" && primary && (
        <div className="mt-3 p-3 rounded-md text-sm" style={{ background: C.panel, border: `1px solid ${C.border}` }} dir={c.lang === "ar" ? "rtl" : "ltr"}>
          <div className="text-xs mb-1" style={{ color: C.muted }} dir="ltr">Original notice ({c.lang}), kept on the record</div>
          <div className="font-medium">{primary.title}</div>
          <div className="text-xs mt-1" style={{ color: C.muted }}>{primary.body}</div>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 text-sm">
        <div>
          <div className="text-xs mb-1" style={{ color: C.muted }}>Summary (English)</div>
          {sel.editing ? <textarea rows={5} style={{ ...inputStyle, width: "100%" }} value={e.summary} onChange={(x) => setE("summary", x.target.value)} /> : <p className="leading-relaxed">{c.summary}</p>}
          <div className="text-xs mt-3 mb-1" style={{ color: C.muted }}>Estimated value (USD)</div>
          {sel.editing ? <input type="number" style={{ ...inputStyle, width: 160 }} value={e.value} onChange={(x) => setE("value", x.target.value)} /> : <div>{c.value ? c.value.toLocaleString() : "not stated"}</div>}
          {c.flags.length > 0 && <div className="mt-3 flex gap-2 flex-wrap">{c.flags.map((f) => <Pill key={f} color={C.red}>{f}</Pill>)}</div>}
        </div>
        <div>
          <div className="text-xs mb-1" style={{ color: C.muted }}>Matched PFM functions, with evidence</div>
          {c.matchedFunctions.length === 0 ? <div style={{ color: C.muted }}>none</div> : c.matchedFunctions.map((m, i) => <div key={i} className="py-0.5"><span className="font-medium">{functions.find((f) => f.id === m.function_id)?.name ?? m.function_id}</span> <span className="text-xs" style={{ color: C.muted }}>"{m.evidence}"</span></div>)}
          {c.systemNames.length > 0 && <div className="mt-2 flex gap-2 flex-wrap">{c.systemNames.map((s) => <Pill key={s} color={C.purple}>{s}</Pill>)}</div>}
          <div className="text-xs mt-3 mb-1" style={{ color: C.muted }}>{linked.length > 1 ? `Duplicate cluster, ${linked.length} sources` : "Source"}</div>
          {linked.map((n) => <div key={n.id} className="text-xs py-0.5"><span className="font-medium">{srcName(n.source)}</span> <a href={n.url} target="_blank" rel="noreferrer" style={{ color: C.blue }}>{n.externalId}</a>{n.id !== c.primaryNoticeId && <span style={{ color: C.muted }}> joined by {c.matches.find((m) => m.noticeId === n.id)?.method}</span>}</div>)}
        </div>
      </div>
      <div className="mt-4">
        <div className="text-sm font-medium mb-2">Proposed Opportunity record <span className="text-xs font-normal" style={{ color: C.muted }}>— what Approve sends to Zoho, once wired in week 3</span></div>
        <OpportunityPreview sections={preview} />
      </div>
      <div className="mt-4 p-3 rounded-md" style={{ border: `2px solid ${C.orange}`, background: C.orange + "11" }}>
        <div className="flex items-center gap-2 text-sm font-medium mb-3" style={{ color: C.orange }}><Lock size={14} />Human checkpoint. Reviewer: {reviewer.trim() || "not set, enter a name in the control row"}</div>
        <div className="flex gap-2 flex-wrap items-center">
          <Btn color={C.orange} icon={Check} onClick={() => decide(c, "approve", sel.editing ? edits : null)}>{sel.editing ? "Approve with edits" : "Approve"}</Btn>
          {!sel.editing && <Btn outline color={C.orange} icon={Pencil} onClick={() => setSel((s) => ({ ...s, editing: true, edits: e }))}>Edit then approve</Btn>}
          {sel.editing && <Btn outline color={C.muted} onClick={() => setSel((s) => ({ ...s, editing: false, edits: null }))}>Discard edits</Btn>}
          <span className="ml-auto inline-flex gap-2 items-center">
            <select style={{ ...inputStyle, padding: "6px 8px" }} value={sel.reason} onChange={(x) => setSel((s) => ({ ...s, reason: x.target.value }))}><option value="">Reason (required)</option>{REASONS.map((r) => <option key={r}>{r}</option>)}</select>
            <Btn outline color={C.red} icon={X} onClick={() => decide(c, "reject", null, sel.reason)}>Reject</Btn>
          </span>
        </div>
      </div>
    </Card>
  );
}
