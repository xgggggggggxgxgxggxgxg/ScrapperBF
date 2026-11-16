// Données de démonstration (extraites des publications officielles DGCMEF, novembre 2026)
window.DATA = {
  user:{name:'Ayedji Jaciel', initials:'AJ', role:'Utilisateur', email:'jacileonayedji@gmail.com', plan:'Découverte', since:'15/11/2026'},
  lastUpdate:'15 novembre 2026 · 11:20',
  stats:{total:19914, open:95, awarded:17668, amount:'2600.08 Mds FCFA', newWeek:195, volEst:'1 466,2 Md FCFA', volAtt:'2 600,1 Md FCFA'},
  ministries:[['Ministère de la Santé',1415],["Ministère de l'Économie et des Finances",1393],["Ministère de l'Agriculture",692],["Ministère de l'Eau",417],["Ministère de l'Éducation",302]],
  regions:[['Kadiogo',3932],['Bankui',267],['Guiriko',238],['Nando',204],['Nakambé',187]],
  series:[260,250,300,280,230,330,290,420,310,350,270,310,330,350,380,420,400,330,320,380,350,340,330,310,330,250,240,330,430,400,470,420,350,330,360,330,380,300,330,1150,650,980,850,360],
  seriesLabels:['Fév.','Juin','Oct.','Fév.','Juin','Oct.','Fév.','Juin','Oct.','Fév.','Juin','Oct.','Fév.','Juin','Nov.'],
  types:[['Attributions',87.3,'#dc2626'],['Autres',2.1,'#059669'],['Avis',5.2,'#2563eb'],["Avis d'annulation",0.4,'#15603d'],['Avis de présélection',0.6,'#7c3aed'],['Demandes de prix',3.2,'#c7952a'],['Résultats provisoires',1.2,'#0d4a2e']],
  markets:[
    {id:44227,title:"Réalisation des travaux d'entretien et réparation de bâtiments",authority:'CENTRE DE CONTROLE DES VEHICULES AUTOMOBILES',ref:'2026-00015/CCVA/DG/SAGSL',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:68110000,status:'open',region:'Toutes régions',sector:'BTP',pub:'2026-11-13'},
    {id:44232,title:"Constitution d'un répertoire de prestataires et de fournisseurs",authority:'DIRECTION REGIONALE DE LA POLICE NATIONALE DES TANNOUNYAN',ref:'2026-001/MSECU/DGPN/DRPN-TNY',type:'AO',typeLabel:"Avis d'appel d'offres",deadline:'2026-11-23',amount:null,status:'open',region:'Tannounyan',sector:'Services',pub:'2026-11-13'},
    {id:44219,title:'Entretien et la réparation de matériel roulant',authority:'CENTRE DE CONTROLE DES VEHICULES AUTOMOBILES',ref:'2026-0016/CCVA/DG/SAGSL',type:'DP',typeLabel:'Demande de prix',deadline:'2026-12-14',amount:35403600,status:'open',region:'Toutes régions',sector:'Services',pub:'2026-11-13'},
    {id:44221,title:'Frais de transport au profit du CCVA',authority:'CENTRE DE CONTROLE DES VEHICULES AUTOMOBILES',ref:'2026-0017/CCVA/DG/A/SAGSL',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:17372881,status:'open',region:'Kadiogo',sector:'Transport',pub:'2026-11-13'},
    {id:44222,title:"Entretien et réparation de matériels et d'installations techniques",authority:'CENTRE DE CONTROLE DES VEHICULES AUTOMOBILES',ref:'2026-0018/CCVA/DG/SAGSL',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:26300000,status:'open',region:'Kadiogo',sector:'Services',pub:'2026-11-13'},
    {id:44223,title:'Acquisition de matériel',authority:'CENTRE DE CONTROLE DES VEHICULES AUTOMOBILES',ref:'2026-0019/CCVA/DG/A/SAGSL',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:2236100,status:'open',region:'Kadiogo',sector:'Fournitures',pub:'2026-11-13'},
    {id:44224,title:'Acquisition de gadgets publicitaires',authority:'CENTRE DE CONTROLE DES VEHICULES AUTOMOBILES',ref:'2026-0020/CCVA/DG/SAGSL',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:40125000,status:'open',region:'Kadiogo',sector:'Fournitures',pub:'2026-11-13'},
    {id:44218,title:"Acquisition, installation et mise en service d'échographes portatifs",authority:'Ministère de la Santé',ref:'2026-0050/MS/SG/DMP',type:'AO',typeLabel:"Avis d'appel d'offres",deadline:'2026-12-14',amount:10000000,status:'open',region:'Toutes régions',sector:'Santé',pub:'2026-11-13'},
    {id:44228,title:"Travaux de réfection et d'aménagement de la salle de laboratoire du bâtiment Lamoussa OUATTARA au profit de l'Ecole Nationale de Santé Publique (ENSP) de Koudougou",authority:'Ministère de la Santé',ref:'2026-0051/MS/SG/DMP',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:85535721,status:'open',region:'Toutes régions',sector:'BTP',pub:'2026-11-13',lots:1,financing:'LAD-Grant ID5657-Y2 Budgeted Work Plan',revue:'1165',page:29},
    {id:44209,title:"Réalisation d'un forage positif au CEG de Indini",authority:'Direction Générale du Contrôle des Marchés Publics et des Engagements Financiers',ref:'2026-01/RGRK/PTUY/CKOTI/M/SG/PRCP/CCAM',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:4120000,status:'result',region:'Guiriko',sector:'Hydraulique',pub:'2026-11-13'},
    {id:44226,title:'Travaux de construction de son siège',authority:'SOCIETE NATIONALE DES SUBSTANCES PRECIEUSES (SONASP)',ref:'2026-01/SONASP/DG/DCP',type:'AO',typeLabel:"Avis d'appel d'offres",deadline:'2026-12-28',amount:5000000000,status:'open',region:'Kadiogo',sector:'BTP',pub:'2026-11-13'},
    {id:44201,title:'Entretien et maintenance de véhicules',authority:"MINISTERE DE L'ECONOMIE ET DES FINANCES",ref:'2026-0125/MEF/SG/DMP',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:2000000,status:'result',region:'Kadiogo',sector:'Services',pub:'2026-11-13'},
    {id:44202,title:"Recrutement d'un consultant pour la formation suivie de coaching sur l'audit informatique",authority:"MINISTERE DE L'ECONOMIE ET DES FINANCES",ref:'2026-0128/MEF/SG/DMP',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:null,status:'result',region:'Kadiogo',sector:'Prestations intellectuelles',pub:'2026-11-13'},
    {id:44208,title:"Travaux d'achèvement de trois (03) salles de classes + bureau + magasin à l'école de Poa « A »",authority:'Direction Générale du Contrôle des Marchés Publics et des Engagements Financiers',ref:'2026-02/RGRK/PTUY/CKOTI/M/SG/PRCP/CCAM',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:10676510,status:'result',region:'Guiriko',sector:'BTP',pub:'2026-11-13'},
    {id:44230,title:"Acquisition d'intrants pour la pépinière de l'Ecole Nationale des Eaux et Forêts",authority:'ECOLE NATIONALE DES EAUX ET FORETS (ENEF)',ref:'2026-021/MSECU/SG/ENEF/DG/PRCP',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:3000000,status:'open',region:'Nando',sector:'Agriculture',pub:'2026-11-13'},
    {id:44211,title:"Conception et réalisation d'une bibliothèque virtuelle",authority:'COMMUNE DE OUAGADOUGOU',ref:'2026-03/CO/M/DCP',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:null,status:'result',region:'Kadiogo',sector:'Informatique',pub:'2026-11-13'},
    {id:44225,title:"Travaux de réalisation d'un forage positif à gros débit à la Cour d'Appel de Fada N'Gourma",authority:'MINISTERE DE LA JUSTICE',ref:'2026-032/MJ/SG/DMP',type:'DP',typeLabel:'Demande de prix',deadline:'2026-11-23',amount:17420000,status:'open',region:'Goulmou',sector:'Hydraulique',pub:'2026-11-13'},
    {id:44212,title:"Mise en place d'un système intégré de gestion d'audit interne",authority:'COMMUNE DE OUAGADOUGOU',ref:'2026-04/CO/M/DCP',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:null,status:'result',region:'Kadiogo',sector:'Informatique',pub:'2026-11-13'},
    {id:44214,title:'Travaux de construction de deux (02) salles de classe',authority:'Commune de Rouko',ref:'2026-04/RKLS/PBAM/CRKO/PRCP',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:11271186,status:'result',region:'Bankui',sector:'BTP',pub:'2026-11-13'},
    {id:44217,title:'Acquisition de kits métiers au profit des jeunes de la Région du Centre-Sud',authority:'MINISTERE SANTE',ref:'2026-04/RKLS/PBAM/CRKO/PRCP',type:'RP',typeLabel:'Résultat provisoire',deadline:'2026-11-13',amount:15000000,status:'result',region:'Centre-Sud',sector:'Fournitures',pub:'2026-11-13'}
  ],
  authorities:[
    ['CENTRE DE CONTROLE DES VEHICULES AUTOMOBILES','Organisme public','—',6,'—','190 M FCFA'],
    ['Ministère de la Santé','Ministère','—',2,'—','96 M FCFA'],
    ['Direction Générale du Contrôle des Marchés Publics et des Engagements Financiers','Direction','GUIRIKO',2,'—','15 M FCFA'],
    ["MINISTERE DE L'ECONOMIE ET DES FINANCES",'Ministère','—',2,'—','2 M FCFA'],
    ['COMMUNE DE OUAGADOUGOU','Mairie','—',2,'—','—'],
    ['DIRECTION REGIONALE DE LA POLICE NATIONALE DES TANNOUNYAN','Direction','REGION DES TANNOUNYAN',1,'—','—'],
    ['SOCIETE NATIONALE DES SUBSTANCES PRECIEUSES (SONASP)','Organisme public','—',1,'—','5.0 Md FCFA'],
    ['ECOLE NATIONALE DES EAUX ET FORETS (ENEF)','Organisme public','—',1,'—','3 M FCFA'],
    ['MINISTERE DE LA JUSTICE','Ministère','—',1,'—','17 M FCFA'],
    ['Commune de Rouko','Mairie','BANKUI',1,'—','11 M FCFA'],
    ['MINISTERE SANTE','Ministère','—',1,'—','15 M FCFA']
  ],
  bulletins:[
    {date:'2026-11-13',num:'4505',avis:38,resultats:21},
    {date:'2026-11-12',num:'4504',avis:27,resultats:15},
    {date:'2026-11-11',num:'4503',avis:31,resultats:12},
    {date:'2026-11-10',num:'4502',avis:19,resultats:24},
    {date:'2026-11-09',num:'4501',avis:33,resultats:9}
  ]
};
// --- Experts agréés : professionnels vérifiés pour le montage des dossiers d'appel d'offres ---
window.DATA.experts=[
  {name:'Boukary Ouédraogo',initials:'BO',role:'Ingénieur génie civil',sectors:['BTP','Hydraulique'],region:'Ouagadougou',years:14,rating:4.9,dossiers:212,langues:'Français, Mooré',bio:'Montage technique et financier de DAO travaux : mémoires techniques, plannings, cadres de devis.',rate:'À partir de 150 000 FCFA / dossier',phone:'+226 70 11 22 33'},
  {name:'Awa Sanou',initials:'AS',role:'Juriste marchés publics',sectors:['Services','Prestations intellectuelles'],region:'Bobo-Dioulasso',years:11,rating:4.8,dossiers:168,langues:'Français, Dioula',bio:'Conformité administrative, pièces juridiques, recours et contentieux des marchés publics.',rate:'À partir de 120 000 FCFA / dossier',phone:'+226 76 44 55 66'},
  {name:'Idrissa Compaoré',initials:'IC',role:'Expert-comptable',sectors:['Fournitures','Services'],region:'Ouagadougou',years:17,rating:4.9,dossiers:301,langues:'Français',bio:'États financiers, attestations fiscales et sociales, analyse de la capacité financière.',rate:'À partir de 90 000 FCFA / dossier',phone:'+226 70 77 88 99'},
  {name:'Mariam Traoré',initials:'MT',role:'Rédactrice d\'offres',sectors:['Santé','Éducation','Services'],region:'Koudougou',years:8,rating:4.7,dossiers:124,langues:'Français, Anglais',bio:'Rédaction et mise en forme des offres techniques, notes méthodologiques, chronogrammes.',rate:'À partir de 80 000 FCFA / dossier',phone:'+226 65 10 20 30'},
  {name:'Seydou Zongo',initials:'SZ',role:'Métreur / Projeteur BTP',sectors:['BTP'],region:'Ouahigouya',years:12,rating:4.6,dossiers:143,langues:'Français, Mooré',bio:'Métrés, bordereaux de prix, sous-détails de prix et optimisation des coûts travaux.',rate:'À partir de 110 000 FCFA / dossier',phone:'+226 78 32 14 05'},
  {name:'Fatoumata Diallo',initials:'FD',role:'Consultante QHSE',sectors:['BTP','Industrie'],region:'Ouagadougou',years:9,rating:4.8,dossiers:97,langues:'Français, Anglais',bio:'Plans qualité, hygiène, sécurité et environnement exigés dans les DAO travaux et services.',rate:'À partir de 100 000 FCFA / dossier',phone:'+226 70 55 66 77'},
  {name:'Rasmané Kaboré',initials:'RK',role:'Spécialiste passation',sectors:['Prestations intellectuelles','Services'],region:'Ouagadougou',years:15,rating:4.9,dossiers:256,langues:'Français, Anglais',bio:'Ancien membre de commission d\'attribution : relecture critique des offres avant dépôt.',rate:'À partir de 160 000 FCFA / dossier',phone:'+226 76 90 80 70'},
  {name:'Clarisse Ouattara',initials:'CO',role:'Ingénieure informatique',sectors:['Informatique'],region:'Bobo-Dioulasso',years:7,rating:4.7,dossiers:68,langues:'Français, Anglais',bio:'Réponses aux marchés de systèmes d\'information : architecture, planning, références projets.',rate:'À partir de 130 000 FCFA / dossier',phone:'+226 65 44 33 22'},
  {name:'Hamidou Barry',initials:'HB',role:'Agronome / Hydraulicien',sectors:['Agriculture','Hydraulique'],region:'Fada N\'Gourma',years:13,rating:4.8,dossiers:151,langues:'Français, Fulfuldé',bio:'Montage de dossiers forages, aménagements et intrants agricoles pour collectivités et ONG.',rate:'À partir de 115 000 FCFA / dossier',phone:'+226 70 12 34 56'}
];
// --- Périmètre d'accès par plan + archive documentaire DGCMEF ---
// Base des bulletins « Quotidien » (PDF d'avis d'appels d'offres) publiés depuis 2022.
// La liste des bulletins est générée dans directory.html à partir de cet ancrage.
window.DATA.archive={
  recent:20, year2026:3240, full:19914,
  bulletins:1182, firstYear:2022, lastYear:2026,
  anchorDate:'2026-11-13', anchorNum:4505, totalPages:38460
};
window.fmtAmount=function(n){ if(n==null) return '—'; if(n>=1e9) return (n/1e9).toLocaleString('fr-FR',{maximumFractionDigits:1})+' Md FCFA'; if(n>=1e6) return (n/1e6).toLocaleString('fr-FR',{maximumFractionDigits:1})+' M FCFA'; return n.toLocaleString('fr-FR')+' FCFA'; };
window.fmtDate=function(iso){ var p=iso.split('-'); return p[2]+'/'+p[1]+'/'+p[0]; };
window.daysLeft=function(iso){ var t=new Date(iso+'T23:59:59'); return Math.ceil((t-Date.now())/86400000); };
