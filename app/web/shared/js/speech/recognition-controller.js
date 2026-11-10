(function(){
  'use strict';

  // v110 in-place fix: this file remains js/25; no extra JS files are added.
  // It owns lecture start/pause/resume/stop, yellow highlight, and top-mode switch safety stop.
  window.__VQ105_INPLACE_LECTURE_MASTER__ = window.__VQ105_INPLACE_LECTURE_MASTER__ || {active:false, running:false, paused:false};

  const LESSONS_V105 = {"basic":{"title":"Course 1: Gas Exchange Framework","keywords":["gas exchange","氣體交換","basic","framework","ABG"],"script":"大家好。這一單元先建立氣體交換的基本架構。\n\n解讀氣體交換時，不能只看 PaO2 或 PaCO2，而要一起考慮四個層次：通氣、灌流、擴散，以及血液的氧含量。\n\n第一，通氣代表新鮮氣體能不能有效到達肺泡。第二，灌流代表血液能不能到達肺泡周圍的微血管。第三，擴散代表氧氣能不能穿過肺泡微血管膜。第四，氧含量代表血液是否有足夠的血紅素攜帶氧氣。\n\n當病人出現低血氧時，不要立刻假設只有肺泡氧分壓下降，或只有血紅素異常；應該先判斷氣體交換路徑的哪一個步驟受到影響。\n\n後面的課程都會回到這個架構：通氣影響 PaCO2 與 PAO2；灌流影響 V/Q；擴散影響 A-a gradient；血紅素則影響 CaO2 與 oxygen delivery。"},"ventilation":{"title":"Course 2: Ventilation and Alveolar Ventilation","keywords":["ventilation","通氣","alveolar ventilation","VA","VT","VD","respiratory rate","dead space"],"script":"大家好。這一單元說明通氣與肺泡通氣。\n\n每分鐘通氣量是每分鐘進出呼吸系統的總氣體量，可以寫成 VE = VT × f，其中 VT 是潮氣容積，f 是呼吸頻率。\n\n但是吸入的氣體並不是全部都參與肺泡氣體交換。位在死腔中的氣體不會和肺微血管血液進行有效交換。\n\n因此，真正直接影響二氧化碳清除的是肺泡通氣量：VA = (VT − VD) × f。\n\n當 VT 下降、VD 增加，或呼吸頻率下降時，VA 會降低。VA 降低時，二氧化碳容易滯留，PaCO2 會上升；VA 增加時，二氧化碳排除增加，PaCO2 會下降。\n\n以模型的正常設定為例，VA = (500 − 150) × 15 / 1000 = 5.25 公升每分鐘。使用 V/Q Visual Model 時，可以把 VT、VD 與呼吸頻率的改變和這個基準肺泡通氣量一起理解。"},"alveolar_gas":{"title":"Course 3: Alveolar Gas Equation and Oxygenation","keywords":["alveolar gas","PAO2","FiO2","oxygenation","altitude","inspired oxygen"],"script":"大家好。這一單元說明肺泡氣體方程式與氧合。\n\n肺泡氧分壓可以用肺泡氣體方程式估算：PAO2 = FiO2 × (Patm − PH2O) − PaCO2 / R。\n\n這裡有三個重點。第一，FiO2 增加會提高 PAO2，所以補充氧氣可以提高肺泡氧分壓。第二，PaCO2 增加會使 PAO2 降低，因此低通氣造成二氧化碳滯留時，肺泡氧分壓也會下降。第三，在高海拔環境，氣壓降低，所以吸入氧分壓與 PAO2 都會下降。\n\n氧合問題和通氣問題不能視為同一件事。提高 FiO2 可以改善氧合，但不會直接降低 PaCO2；PaCO2 主要仍由肺泡通氣量控制。"},"perfusion":{"title":"Course 4: Perfusion, Cardiac Output, and Mixed Venous Blood","keywords":["perfusion","灌流","cardiac output","mixed venous","SvO2","PvO2"],"script":"大家好。這一單元介紹灌流、心輸出量與混合靜脈血。\n\n灌流是到達已通氣肺泡周圍微血管的血流。如果肺泡仍有通氣，但灌流下降，局部 V/Q 會升高，嚴重時會接近死腔生理。\n\n心輸出量也會影響全身氧氣運送。氧氣運送量大致等於心輸出量乘以動脈血氧含量。\n\n當心輸出量下降時，PaO2 和 PaCO2 不一定立刻出現非常明顯的變化，但混合靜脈血氧值可能明顯下降。因為組織仍需要氧氣，較低的血流量會使組織從每一單位血液中抽取更多氧氣，因此混合靜脈 PvO2 與 SvO2 會下降。\n\n所以混合靜脈血的數值可以幫助我們判斷目前氧氣運送是否足以滿足組織需求。"},"normal_vq":{"title":"Course 5: Normal V/Q Gradient — Apex and Base","keywords":["apex","base","gradient","normal V/Q","肺尖","肺底"],"script":"大家好。這一單元說明正常肺臟的 V/Q 梯度。\n\nV/Q ratio 是通氣量除以灌流量。在直立的肺臟中，從肺尖到肺底，通氣和灌流都會受到重力影響。\n\n在肺尖，通氣較少，灌流也較少，但是灌流下降的比例更大，因此肺尖的 V/Q 相對較高。\n\n在肺底，通氣增加，灌流也增加，而且灌流增加的比例更大，因此肺底的 V/Q 相對較低。\n\n所以正常肺臟並不是每一個區域都有相同的 V/Q。肺尖通常屬於較高 V/Q，肺底屬於較低 V/Q。肺尖 V/Q 較高並不代表肺尖對總氣體交換的貢獻最大，因為肺底通常同時接受更多的總通氣與總灌流。"},"low_vq":{"title":"Course 6: Low V/Q and Shunt-like Physiology","keywords":["low V/Q","shunt-like","pneumonia","asthma","COPD","airway obstruction"],"script":"大家好。這一單元說明 low V/Q 與 shunt-like physiology。\n\nLow V/Q 代表相對於灌流而言，通氣不足。最簡單的理解方式就是：血流還在，但是氣體不夠。\n\n因為血液仍然流經通氣不足的肺泡，所以離開該區域的血液無法完全氧合。局部 PO2 會下降，PCO2 傾向上升，氧含量也會降低。\n\n如果通氣變得非常低，但灌流仍持續存在，low V/Q 就會逐漸接近 shunt-like physiology，極端時可接近 true shunt。\n\n常見例子包括肺炎、氣喘、COPD 與局部呼吸道阻塞。因此在 V/Q Visual Model 中，low V/Q 或 shunt-like 狀態主要應理解為「通氣不足但灌流保留」的區域。"},"high_vq":{"title":"Course 7: High V/Q, Dead Space, and Pulmonary Embolism","keywords":["high V/Q","dead space","pulmonary embolism","PE"],"script":"大家好。這一單元說明 high V/Q、dead space 與 pulmonary embolism。\n\nHigh V/Q 代表通氣相對保留，但是灌流不足。最簡單的理解方式就是：氣體到得了肺泡，但是血流不夠。\n\n當灌流變得非常低時，受影響區域會接近 dead-space physiology。也就是空氣可以進入肺泡，但缺乏足夠血流參與氣體交換。\n\n肺栓塞是典型例子。栓子會降低肺血流，但通氣可能相對保留，因此 V/Q 上升，局部逐漸呈現 high V/Q 或 dead-space-like 的狀態。\n\n所以在 V/Q Visual Model 中，dead-space-like physiology 應該呈現為「有通氣、但灌流不足」的區域，而不是 low V/Q 的通氣不足區域。"},"shunt":{"title":"Course 8: True Shunt and the Shunt Equation","keywords":["true shunt","shunt equation","Qs/Qt","right-to-left shunt"],"script":"大家好。這一單元介紹 true shunt 與 shunt equation。\n\nShunt 的核心是血流存在，但是有效通氣缺失。最簡單的記法就是：有血，但是沒有足夠的氣體和這些血液進行交換。\n\n分流的血液沒有接觸到有效通氣的肺泡，因此在混入動脈血之前無法充分氧合，會造成明顯的 PaO2 下降。\n\nTrue shunt 對提高 FiO2 的反應通常比 low V/Q 差，因為真正被分流的血液根本沒有流經有效通氣的肺泡。\n\nShunt equation 可以寫成 Qs/Qt = (CcO2 − CaO2) / (CcO2 − CvO2)。這個公式使用氧含量，而不是只看 PO2，因為血液中的氧大部分是由血紅素攜帶。"},"diffusion":{"title":"Course 9: Diffusion Limitation and the A-a Gradient","keywords":["diffusion","diffusion limitation","A-a gradient","DLCO","exercise"],"script":"大家好。這一單元說明 diffusion limitation 與 A-a gradient。\n\n擴散是氣體穿過肺泡微血管膜的過程。氧氣由肺泡進入血液，而二氧化碳則由血液進入肺泡。\n\n當肺臟的擴散能力下降時，氧氣可能無法在有限時間內和肺泡氣體達到充分平衡，因此動脈 PaO2 下降。此時 PAO2 可能仍相對正常，所以 A-a gradient 會增加。\n\n運動時氧氣消耗增加，而且肺微血管通過時間縮短，因此 diffusion limitation 可能變得更明顯。\n\n提高 FiO2 可以增加氧分壓梯度，進而改善氧氣轉移。二氧化碳通常較不容易受到單純擴散障礙影響，因為二氧化碳的擴散能力較高。"},"oxygen_content":{"title":"Course 10: Hemoglobin, CaO2, and Oxygen Delivery","keywords":["hemoglobin","Hb","CaO2","oxygen content","oxygen delivery","anemia","SaO2"],"script":"大家好。這一單元說明血紅素、動脈血氧含量 CaO2，以及 oxygen delivery。\n\nPaO2 代表溶解在血漿中的氧分壓，它不等於血液所攜帶的總氧量。\n\n動脈血氧含量主要由血紅素濃度和血紅素氧飽和度決定，可以近似表示為：CaO2 ≈ 1.34 × Hb × SaO2 + 0.003 × PaO2。\n\n在貧血時，PaO2 可以維持正常，SaO2 也可能正常，但是因為血紅素濃度下降，CaO2 仍然會降低。\n\n氧氣運送量大致等於心輸出量乘以 CaO2。因此，不論是血紅素過低，或心輸出量過低，都可能降低組織氧氣運送。這也是為什麼只看 PaO2 可能低估實際的 oxygen delivery 問題。"},"integrated_disease":{"title":"Course 11: Integrated Disease Physiology — PE, Pneumonia, Asthma, and COPD","keywords":["disease","PE","pneumonia","asthma","COPD"],"script":"大家好。最後這一單元整合 pulmonary embolism、pneumonia、asthma、COPD 與 V/Q physiology。\n\n肺栓塞主要降低灌流。通氣相對保留，因此 V/Q 上升，受影響的區域會呈現 high V/Q 或 dead-space-like physiology。\n\n肺炎會因發炎、液體或實變而降低局部通氣，但灌流仍可能存在，因此通常偏向 low V/Q 或 shunt-like physiology。\n\n氣喘與 COPD 常伴隨呼吸道阻塞，受影響區域的通氣下降，因此常見 regional low V/Q。COPD 另外可能合併肺泡破壞、dead space 增加與氣體交換效率下降，所以有時呈現混合型異常。\n\n總結來說：肺栓塞主要降低 Q，使 V/Q 上升；肺炎主要降低 V，使 V/Q 下降；氣喘與 COPD 常造成局部 low V/Q，也可能同時合併其他型態的氣體交換異常。"}};

  const $ = id => document.getElementById(id);
  const synth = window.speechSynthesis;

  let lessonText = '';
  let lessonIndex = 0;
  let segments = [];
  let currentSegment = -1;
  let lessonTimer = null;
  let lessonUtterance = null;
  let lectureRunning = false;
  let lecturePaused = false;
  let timerBaseIndex = 0;
  let timerStartedAt = 0;
  let timerTotalMs = 10000;
  let boundaryMode = false;
  let mouthTimer = null;
  let mouthCounter = 0;
  let answerUtterance = null;
  let answerText = "";
  let answerIndex = 0;
  let controlTimer = null;
  let installed = false;

  window.__VQ105_INPLACE_LECTURE_MASTER__ = window.__VQ105_INPLACE_LECTURE_MASTER__ || {};

  function clean(s){ return String(s || '').trim(); }

  // v216: student-facing answers must be plain readable text. Models sometimes
  // emit TeX/LaTeX even when asked not to; normalize common medical notation and
  // remove residual math delimiters/backslash commands before display and TTS.
  function plainStudentAnswer(value){
    if(window.VQ_UPLOADED_COURSE_ACTIVE&&window.CourseContent)return window.CourseContent.display(value);
    const math=window.CourseMath?window.CourseMath.protect(value):null;
    let t=String(math?math.text:value||'').trim();
    t=t.replace(/```[a-z0-9_-]*\s*/gi,'').replace(/```/g,'').replace(/`/g,'');
    t=t.replace(/\\dot\s*\{\s*V\s*\}\s*_\s*\{?\s*A\s*\}?/gi,'VA');
    t=t.replace(/\\dot\s*V\s*_\s*\{?\s*A\s*\}?/gi,'VA');
    t=t.replace(/\\dot\s*\{\s*Q\s*\}/gi,'Q');
    t=t.replace(/\\dot\s*Q/gi,'Q');
    t=t.replace(/\\dot\s*\{\s*V\s*\}/gi,'V');
    t=t.replace(/\\dot\s*V/gi,'V');
    for(let i=0;i<3;i++) t=t.replace(/\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}/g,'$1/$2');
    t=t.replace(/\\(?:mathrm|text|operatorname)\s*\{([^{}]*)\}/g,'$1');
    t=t.replace(/\\Delta\b/g,'Δ').replace(/\\alpha\b/g,'alpha').replace(/\\beta\b/g,'beta');
    t=t.replace(/\\times\b/g,'×').replace(/\\cdot\b/g,'·');
    t=t.replace(/\\(?:left|right|quad|qquad)\b/g,'');
    t=t.replace(/\\[(),;!\[\]]/g,'');
    t=t.replace(/\$/g,'');
    t=t.replace(/_\s*\{\s*([A-Za-z0-9]+)\s*\}/g,'$1');
    t=t.replace(/_\s*([A-Za-z0-9]+)/g,'$1');
    t=t.replace(/[{}]/g,'');
    t=t.replace(/\\([A-Za-z]+)/g,'$1').replace(/\\/g,'');
    // Student UI shows only physiological content; remove figure/table/page/source phrasing.
    const sourceLeakPatterns=[
      /\b(?:as\s+(?:shown|illustrated|depicted)\s+in|according\s+to|see)\s+(?:the\s+)?(?:figure|fig\.?|table)\s*[A-Za-z0-9.\-]*\s*[,;:]?\s*/gi,
      /\b(?:the\s+)?(?:figure|fig\.?|table)\s*[A-Za-z0-9.\-]*\s+(?:shows|illustrates|demonstrates|indicates|depicts)\s+(?:that\s+)?/gi,
      /\b(?:on\s+)?page\s+\d+[A-Za-z]?\b\s*[,;:]?\s*/gi,
      /\b(?:in\s+)?chapter\s+\d+[A-Za-z]?\b\s*[,;:]?\s*/gi,
      /\b(?:in|from|according\s+to)\s+(?:the\s+)?(?:textbook|reference\s+book|source|reference)\b\s*[,;:]?\s*/gi,
      /(?:如|正如)\s*(?:圖|图|Figure|Fig\.?|表|Table)\s*[A-Za-z0-9.\-]*\s*(?:所示|顯示|显示)[，,：:]?\s*/g,
      /(?:第\s*)?[A-Za-z0-9.\-]+\s*(?:圖|图|表)\s*(?:顯示|显示|指出|說明|说明)[，,：:]?\s*/g,
      /(?:第\s*)?\d+\s*(?:頁|页|章)\s*[，,：:]?\s*/g,
      /(?:根據|依据|依據|依照)\s*(?:教材|教科書|教科书|參考書|参考书|來源|来源)[，,：:]?\s*/g      ,/(?:根據|依据|依據|依照)?\s*(?:參考文本|参考文本|參考內容|参考内容|參考資料|参考资料|reference\s+text)\s*[,，:：]?\s*/gi
      ,/(?:根據|依据|依據|依照)?\s*(?:表格|表|Table)\s*[A-Za-z0-9.\-]+(?:\s*(?:和|與|及)\s*(?:段落|內容|說明|文字))?\s*(?:指出|說明|显示|顯示)?\s*[,，:：]?\s*/gi
      ,/(?:根據|依据|依據|依照)?\s*(?:段落|內容|說明|文字)\s*(?:指出|說明|显示|顯示)\s*[,，:：]?\s*/g
    ];
    for(const p of sourceLeakPatterns) t=t.replace(p,'');
    t=t.replace(/\bV\s*_\s*\{?A\}?\b/gi,'VA')
       .replace(/\bV\s*_\s*\{?E\}?\b/gi,'VE')
       .replace(/\bV\s*_\s*\{?T\}?\b/gi,'VT')
       .replace(/\bV\s*_\s*\{?D\}?\b/gi,'VD');
    t=t.replace(/^(?:和|與|及)?\s*(?:段落|內容|說明|文字)?\s*(?:指出|說明|顯示|显示)\s*[,，:：]?\s*/,'');
    t=t.replace(/[ \t]+/g,' ').replace(/\s+([.,;:!?，。；：！？])/g,'$1').replace(/\n[ \t]+/g,'\n').trim();
    return math?window.CourseMath.restore(t,math.saved):t;
  }

  function setStatus(msg){
    const s = $('status');
    if(s) s.textContent = 'Status: ' + msg;
  }

  function selectedVoice(){
    const sel = $('voiceSelect');
    const voices = (synth && synth.getVoices) ? synth.getVoices() : [];
    if(sel){
      const direct = voices[Number(sel.value)];
      if(direct) return direct;
      const optText = sel.selectedIndex >= 0 && sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent || '' : '';
      const byText = voices.find(v => optText.includes(v.name || '') && optText.includes(v.lang || ''));
      if(byText) return byText;
    }
    return voices.find(v => /Yating|Taiwan|Traditional|zh-TW|Chinese/i.test((v.name || '') + ' ' + (v.lang || ''))) ||
           voices.find(v => /^zh/i.test(v.lang || '')) ||
           voices[0] || null;
  }

  function rate(){
    const r = parseFloat(($('rateRange') || {}).value || '1');
    return Number.isFinite(r) && r > 0 ? r : 1;
  }

  function pitch(){
    const p = parseFloat(($('pitchRange') || {}).value || '1');
    return Number.isFinite(p) && p > 0 ? p : 1;
  }

  function injectStyles(){
    if(document.getElementById('vq109-lecture-style')) return;
    const st = document.createElement('style');
    st.id = 'vq109-lecture-style';
    st.textContent = `
      #lessonText.vq109-script-box{
        white-space: pre-wrap !important;
        line-height: 1.82 !important;
        overflow-wrap: break-word !important;
        word-break: normal !important;
      }
      #lessonText .vq109-seg{
        border-radius: 8px;
        padding: 1px 3px;
        transition: background-color .18s ease, box-shadow .18s ease;
      }
      #lessonText .vq109-current{
        background: #fff176;
        color: #0f172a;
        box-shadow: 0 0 0 2px rgba(250, 204, 21, .28);
      }
      #lessonText .vq109-done{
        color: #334155;
      }
    `;
    document.head.appendChild(st);
  }

  function mouthEl(){ return document.querySelector('.mouth'); }

  function setMouth(shape){
    const m = mouthEl();
    if(!m) return;
    m.classList.remove('mouth-closed','mouth-small','mouth-open','mouth-wide','mouth-round','mouth-a','mouth-e','mouth-o','mouth-fv');
    m.classList.add(shape || 'mouth-closed');
  }

  function startMouthLoop(kind){
    stopMouthLoop(false);
    document.body.classList.add('speaking');
    const shapes = ['mouth-small','mouth-open','mouth-wide','mouth-round','mouth-open','mouth-small'];
    mouthTimer = setInterval(()=>{
      mouthCounter++;
      let ch = '';
      if(kind === 'lecture' && lessonText){
        ch = lessonText.charAt(Math.min(lessonIndex, lessonText.length - 1)).toLowerCase();
      }else if(kind === 'answer' && answerText){
        ch = answerText.charAt(Math.min(answerIndex, answerText.length - 1)).toLowerCase();
      }
      if(/[ao啊阿哈]/i.test(ch)) setMouth('mouth-a');
      else if(/[ei欸ㄟㄧ一衣]/i.test(ch)) setMouth('mouth-e');
      else if(/[ouㄡㄛㄨ喔哦嗚]/i.test(ch)) setMouth('mouth-o');
      else if(/[bmpㄅㄆㄇ]/i.test(ch)) setMouth('mouth-m');
      else if(/[fv]/i.test(ch)) setMouth('mouth-small');
      else if(/[\s，。！？、,.!?]/i.test(ch)) setMouth('mouth-closed');
      else if(/[aei]/i.test(ch)) setMouth('mouth-wide');
      else if(/[ou]/i.test(ch)) setMouth('mouth-round');
      else if(/[fv]/i.test(ch)) setMouth('mouth-small');
      else setMouth(shapes[mouthCounter % shapes.length]);
    }, Math.max(90, 170 / rate()));
  }

  function stopMouthLoop(close){
    if(mouthTimer) clearInterval(mouthTimer);
    mouthTimer = null;
    document.body.classList.remove('speaking');
    if(close !== false) setMouth('mouth-closed');
  }

  function buildSegments(text){
    const out = [];
    let start = 0;
    const mathRanges=window.CourseMath?window.CourseMath.ranges(text):[];
    let mathCursor=0;
    for(let i=0;i<text.length;i++){
      while(mathCursor<mathRanges.length&&i>=mathRanges[mathCursor][1])mathCursor++;
      if(mathCursor<mathRanges.length&&i>=mathRanges[mathCursor][0])continue;
      const ch = text[i];
      if(ch === '\n'){
        if(i > start) out.push({start, end:i, text:text.slice(start,i)});
        let j = i;
        while(j < text.length && text[j] === '\n') j++;
        out.push({start:i, end:j, text:text.slice(i,j), newline:true});
        start = j;
        i = j - 1;
      }else if(/[。！？!?]/.test(ch)){
        out.push({start, end:i+1, text:text.slice(start,i+1)});
        start = i + 1;
      }
    }
    if(start < text.length) out.push({start, end:text.length, text:text.slice(start)});
    return out.length ? out : [{start:0, end:text.length, text}];
  }

  function renderScript(text){
    const box = $('lessonText');
    if(!box) return;
    injectStyles();
    box.classList.add('vq109-script-box');
    box.innerHTML = '';
    segments = buildSegments(text);
    currentSegment = -1;
    for(let i=0;i<segments.length;i++){
      const seg = segments[i];
      if(seg.newline){
        box.appendChild(document.createTextNode(seg.text));
      }else{
        const span = document.createElement('span');
        span.className = 'vq109-seg';
        span.dataset.i = String(i);
        span.dataset.start = String(seg.start);
        span.dataset.end = String(seg.end);
        if(window.CourseMath)window.CourseMath.render(span,seg.text);else span.textContent = seg.text;
        box.appendChild(span);
      }
    }
    box.scrollTop = 0;
  }

  function segmentForIndex(idx){
    if(!segments.length) return -1;
    for(let i=0;i<segments.length;i++){
      const seg = segments[i];
      if(seg.newline) continue;
      if(idx >= seg.start && idx < seg.end) return i;
    }
    for(let i=segments.length-1;i>=0;i--){
      if(!segments[i].newline) return i;
    }
    return -1;
  }

  function updateHighlight(idx){
    const box = $('lessonText');
    if(!box) return;
    lessonIndex = Math.max(0, Math.min(idx, lessonText.length));
    const nextSeg = segmentForIndex(lessonIndex);
    if(nextSeg === currentSegment) return;
    currentSegment = nextSeg;
    const spans = box.querySelectorAll('.vq109-seg');
    spans.forEach(sp => {
      const i = Number(sp.dataset.i);
      sp.classList.toggle('vq109-current', i === currentSegment);
      const end = Number(sp.dataset.end || 0);
      sp.classList.toggle('vq109-done', end < lessonIndex);
    });
    const cur = box.querySelector('.vq109-current');
    if(cur){
      // Keep highlighting movement inside the TEACHER SCRIPT box only.
      // scrollIntoView() can move the entire document when rate/pitch changes
      // restart a speech segment, which caused the UI to jump to another area.
      try{
        const top = cur.offsetTop;
        const bottom = top + cur.offsetHeight;
        const viewTop = box.scrollTop;
        const viewBottom = viewTop + box.clientHeight;
        if(top < viewTop + 8) box.scrollTop = Math.max(0, top - 8);
        else if(bottom > viewBottom - 8) box.scrollTop = Math.max(0, bottom - box.clientHeight + 8);
      }catch(_){ }
    }
  }

  function renderLessonAt(i){
    if(!$('lessonText')) return;
    if(($('lessonText').dataset || {}).vq109Text !== lessonText){
      $('lessonText').dataset.vq109Text = lessonText;
      renderScript(lessonText);
    }
    updateHighlight(i);
  }

  function setBtn(id, disabled){
    const b = $(id);
    if(!b) return;
    if(b.dataset.r12Disabled===String(!!disabled)&&b.disabled===!!disabled)return;
    b.dataset.r12Disabled=String(!!disabled);
    b.disabled = !!disabled;
    b.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    b.classList.toggle('vq-control-muted', !!disabled);
    b.style.pointerEvents = disabled ? 'none' : 'auto';
    b.style.opacity = disabled ? '0.38' : '1';
    if(disabled){
      b.classList.remove('active','selected','pressed');
      b.setAttribute('aria-pressed','false');
      try{ b.blur(); }catch(_){ }
    }
  }

  function setLectureControls(active, paused){
    const master = window.__VQ105_INPLACE_LECTURE_MASTER__ || {};
    master.active = true;
    master.running = !!active;
    master.paused = !!paused;
    window.__VQ105_INPLACE_LECTURE_MASTER__ = master;

    setBtn('pauseLessonBtn', !active || !!paused);
    setBtn('resumeLessonBtn', !active || !paused);
    setBtn('stopLessonBtn', !active);
  }

  function enforceControls(){
    if(!window.__VQ105_INPLACE_LECTURE_MASTER__ || !window.__VQ105_INPLACE_LECTURE_MASTER__.active) return;
    setLectureControls(lectureRunning, lecturePaused);
  }

  function stopLectureTimers(){
    if(lessonTimer) clearInterval(lessonTimer);
    lessonTimer = null;
  }

  function cancelSpeech(){
    try{ if(synth) synth.cancel(); }catch(_){ }
  }

  function fallbackTick(){
    if(!lectureRunning || lecturePaused || !lessonText || boundaryMode) return;
    const elapsed = Date.now() - timerStartedAt;
    const remaining = Math.max(0, lessonText.length - timerBaseIndex);
    const next = Math.min(lessonText.length, timerBaseIndex + Math.floor((elapsed / timerTotalMs) * remaining));
    if(next > lessonIndex) renderLessonAt(next);
    if(next >= lessonText.length) finishLecture();
  }

  function getLessonScript(){
    const sel = $('lessonSelect');
    const key = sel ? sel.value : 'basic';
    const uploaded = typeof window.VQ_getActiveCourseLecture === 'function' ? window.VQ_getActiveCourseLecture(key) : null;
    if(uploaded && uploaded.id === key){
      return {key, title: String(uploaded.title || 'Uploaded unit'), script: String(uploaded.script || '')};
    }
    const lesson = LESSONS_V105[key] || Object.values(LESSONS_V105)[0] || {};
    return {key, title: lesson.title || 'Current Course', script: String(lesson.script || '')};
  }

  function startFallbackTimer(fromIndex){
    timerBaseIndex = Math.max(0, Math.min(fromIndex || 0, lessonText.length));
    timerStartedAt = Date.now();
    timerTotalMs = Math.max(2500, (lessonText.length - timerBaseIndex) * 175 / rate());
    stopLectureTimers();
    lessonTimer = setInterval(fallbackTick, 120);
  }

  function speakLectureFromIndex(idx, allowResume){
    if(!synth || !lessonText || idx >= lessonText.length){
      if(idx >= lessonText.length) finishLecture();
      return;
    }

    if(!allowResume){
      cancelSpeech();
    }

    const remaining = lessonText.slice(idx);
    const baseIndex = idx;
    const u = new SpeechSynthesisUtterance(remaining);
    const v = selectedVoice();
    if(v) u.voice = v;
    u.lang = /[\u3400-\u9fff]/.test(remaining) ? 'zh-TW' : 'en-US';
    u.rate = rate();
    u.pitch = pitch();
    lessonUtterance = u;
    boundaryMode = false;

    u.onstart = function(){
      if(!lectureRunning || lecturePaused) return;
      startMouthLoop('lecture');
      setStatus('Lecture in progress — the highlighted sentence follows the spoken segment.');
    };
    u.onboundary = function(e){
      if(!lectureRunning || lecturePaused) return;
      if(typeof e.charIndex === 'number' && e.charIndex >= 0){
        boundaryMode = true;
        renderLessonAt(baseIndex + e.charIndex);
      }
    };
    u.onend = function(){
      if(!lectureRunning || lecturePaused) return;
      finishLecture();
    };
    u.onerror = function(e){
      if(lecturePaused) return;
      if(e && e.error === 'voice-unavailable'){
        stopLectureTimers();
        lectureRunning = false;
        setLectureControls(false, false);
        setStatus((e.role === 'yating' ? 'Yating (female)' : 'Zhiwei (male)') + ' ' + e.language + ' voice is unavailable on this device. Add the matching browser/system voice.');
        return;
      }
      boundaryMode = false;
      setStatus('Speech timing event unavailable; using time-based highlighting.');
    };

    try{ synth.speak(u); }catch(_){ boundaryMode = false; }
  }

  function startLectureFromBeginning(){
    const lesson = getLessonScript();
    lessonText = lesson.script || '';
    if(window.VQ_UPLOADED_COURSE_ACTIVE&&window.CourseContent&&window.CourseContent.issues(lessonText)){setStatus('Content needs source review. Retry preparation.');setLectureControls(false,false);return;}
    lessonIndex = 0;
    currentSegment = -1;
    lectureRunning = true;
    lecturePaused = false;
    boundaryMode = false;
    if(!lessonText){
      const box = $('lessonText'); if(box) box.textContent = 'No lecture script is available for this unit.';
      setLectureControls(false, false);
      return;
    }
    cancelSpeech();
    stopMouthLoop(true);
    const scriptBox=$('lessonText');if(scriptBox)delete scriptBox.dataset.vq109Text;
    renderLessonAt(0);
    setLectureControls(true, false);
    startFallbackTimer(0);
    speakLectureFromIndex(0, false);
    setStatus('Lecture started. Pause and resume are available; highlighting follows the spoken segment.');
  }

  function pauseLectureForQuestion(){
    if(!lectureRunning || lecturePaused) return;
    pauseLecture('Lecture paused for a question. Select “Resume” after the answer to continue.');
  }

  function pauseLecture(customStatus){
    if(!lectureRunning) return;
    try{ fallbackTick(); }catch(_){ }
    lecturePaused = true;
    stopLectureTimers();
    try{ if(synth && ((window.VQ_SERVER_TTS && window.VQ_SERVER_TTS.speaking) || (synth.speaking && !synth.paused))) synth.pause(); }catch(_){ }
    stopMouthLoop(true);
    setLectureControls(true, true);
    setStatus(customStatus || 'Speech and highlighting are paused. Select “Resume” to continue.');
  }

  function resumeLecture(){
    if(!lectureRunning || !lecturePaused || !lessonText || lessonIndex >= lessonText.length){
      setStatus('There is no paused lecture to resume. Select “Start Lecture” to begin again.');
      setLectureControls(false, false);
      return;
    }
    lectureRunning = true;
    lecturePaused = false;
    boundaryMode = false;
    setLectureControls(true, false);
    startFallbackTimer(lessonIndex);

    let resumedNative = false;
    try{
      if(window.VQ_SERVER_TTS && window.VQ_SERVER_TTS.paused){ synth.resume(); resumedNative = true; } else if(synth && synth.paused){ synth.resume(); resumedNative = true; }
    }catch(_){ resumedNative = false; }

    startMouthLoop('lecture');
    setStatus('Lecture audio and highlighting resumed from the paused position.');

    // Chrome/Edge sometimes reports paused=false after pause/cancel but does not continue.
    // If native resume did not really resume, restart from the stored character index.
    setTimeout(()=>{
      if(!lectureRunning || lecturePaused) return;
      const stillSilent = window.VQ_SERVER_TTS ? (!window.VQ_SERVER_TTS.speaking && !window.VQ_SERVER_TTS.paused) : (synth && !synth.speaking && !synth.paused);
      if(!resumedNative || stillSilent){
        speakLectureFromIndex(lessonIndex, false);
      }
    }, 450);
  }

  function stopLecture(){
    lectureRunning = false;
    lecturePaused = false;
    stopLectureTimers();
    cancelSpeech();
    stopMouthLoop(true);
    lessonText = '';
    lessonIndex = 0;
    currentSegment = -1;
    segments = [];
    boundaryMode = false;
    const box = $('lessonText');
    if(box){
      box.classList.remove('vq109-script-box');
      delete box.dataset.vq109Text;
      box.textContent = '';
      box.scrollTop = 0;
    }
    setLectureControls(false, false);
    setStatus('Lecture stopped. Select “Start Lecture” to play it again.');
  }

  function stopRecognitionForModeSwitch(){
    ++voiceStartToken;
    voiceStartPending=false;
    clearStaleVoiceRecognition();
    try{ if(window.VQ_stopAllRecognition) window.VQ_stopAllRecognition('Mode changed; speech recording stopped'); }catch(_){ }
    const ask = $('askBtn'), stop = $('stopAskBtn');
    if(ask) ask.disabled = false;
    if(stop) stop.disabled = true;
    const qBox = $('studentQuestion');
    if(qBox) qBox.classList.remove('listening-mode');
  }

  function resetLessonIntroAfterModeSwitch(){
    const lesson = getLessonScript();
    const box = $('lessonText');
    lessonText = '';
    lessonIndex = 0;
    currentSegment = -1;
    segments = [];
    boundaryMode = false;
    if(box){
      box.classList.remove('vq109-script-box');
      delete box.dataset.vq109Text;
      box.textContent = 'Selected: ' + lesson.title + '. Select “Start Lecture”.';
      box.scrollTop = 0;
    }
  }

  function switchModePanel(tabId){
    const map = {
      lessonTab: 'lessonPanel',
      caseTab: 'casePanel',
      modelTab: 'modelPanel'
    };
    const panelId = map[tabId];
    if(!panelId) return;
    Object.keys(map).forEach(id => {
      const btn = $(id), panel = $(map[id]);
      if(btn) btn.classList.toggle('active', id === tabId);
      if(panel) panel.classList.toggle('active', id === tabId);
    });
    document.body.classList.toggle('v150-model-active', tabId === 'modelTab');
    document.body.classList.toggle('v150-case-active', tabId === 'caseTab');
  }

  function stopForModeSwitch(tabId){
    lectureRunning = false;
    lecturePaused = false;
    stopLectureTimers();
    cancelSpeech();
    stopMouthLoop(true);
    stopRecognitionForModeSwitch();
    setLectureControls(false, false);
    resetLessonIntroAfterModeSwitch();
    switchModePanel(tabId);
    const label = tabId === 'caseTab' ? 'Scenario Practice' : (tabId === 'modelTab' ? 'V/Q Visual Model' : 'Lecture / Free Q&A');
    setStatus('Switched to “' + label + '”. Lecture audio, speech input, lip sync, and highlighting have stopped.');
  }

  function finishLecture(){
    lectureRunning = false;
    lecturePaused = false;
    stopLectureTimers();
    lessonIndex = lessonText.length;
    renderLessonAt(lessonIndex);
    stopMouthLoop(true);
    setLectureControls(false, false);
    setStatus('Lecture completed.');
  }

  function answeringLabel(model){
    const m=String(model||'');
    if(m.indexOf('cloud:')===0){const labels=window.VQ_CLOUD_MODEL_LABELS||{};return String(labels[m.slice(6)]||'the cloud model').split(' · ')[0];}
    try{const label=window.VQ_getSelectedModelLabel&&window.VQ_getSelectedModelLabel();if(label)return String(label).split(' · ')[0]+' (local)';}catch(_){}
    return m.replace('local:','')+' (local)';
  }
  async function submitQuestion(options){
    const opts=options||{};
    const capture=window.__v105_active_recognition;
    if(!opts.force&&(voiceStartPending || (capture && !capture.__vqManualStopRequested && !capture.__vqCancelled))){
      setStatus('Select Stop Recording & Answer to finish your voice question.');return;
    }
    const qBox=$('studentQuestion'),aBox=$('teacherAnswer');
    const q=opts.force?clean(opts.question):
      (window.VQ_readQuestionText?window.VQ_readQuestionText(qBox):clean(qBox?qBox.innerText:''));
    if(!q||q==='Listening...'){
      if(aBox)aBox.textContent='Please type or record a question first.';
      return;
    }
    const source=opts.force?(opts.source||'text'):
      (window.VQ_NEXT_INPUT_SOURCE==='voice'?'voice':'text');
    if(!opts.force)window.VQ_NEXT_INPUT_SOURCE=null;
    const manager=window.VQ_QA_REQUESTS;
    const entry=manager&&manager.begin(q,{force:!!opts.force,source:source});
    if(entry&&entry.joined)return entry.request.promise;
    const request=entry&&entry.request;
    window.VQ_LAST_INPUT_SOURCE=source;
    pauseLectureForQuestion();
    if(aBox)aBox.textContent='The AI teacher is retrieving relevant reference material and preparing the answer...';
    setStatus(request?'Answering with '+answeringLabel(request.model)+'…':'Preparing answer…');
    const task=(async function(){
      let ans='';
      try{
        if(window.VQ_generateFinalAnswer)ans=await window.VQ_generateFinalAnswer(q);
        else if(window.VQ_V55_answerQuestion)ans=await window.VQ_V55_answerQuestion(q);
        else ans='The Q&A engine is not loaded. Please refresh the page.';
      }catch(error){
        if(request&&(!manager.isCurrent(request)||request.controller&&request.controller.signal.aborted))return;
        ans='An error occurred while processing the question. Please submit it again.';
      }
      if(request&&!manager.isCurrent(request))return;
      ans=window.VQ_UPLOADED_COURSE_ACTIVE&&window.CourseContent?
        window.CourseContent.display(ans):plainStudentAnswer(window.VQ_cleanStudentAnswer?
        window.VQ_cleanStudentAnswer(ans):ans);
      if(aBox){if(window.CourseMath)window.CourseMath.render(aBox,ans);else aBox.textContent=ans;aBox.scrollTop=0;}
      if(request&&String(request.model).indexOf('cloud:')===0){
        const status=$('cloudApiKeyStatus');
        const referenceMeta=typeof window.VQ_REFERENCE_LAST_META==='function'?window.VQ_REFERENCE_LAST_META():null;
        const info=window.VQ_LAST_COURSE_ANSWER_META||window.VQ_REFERENCE_BATCH_LAST_META||referenceMeta||{};
        const engine=String(info.engine||'');
        const provider=String(request.model).split(':')[1]||'';
        if(status&&provider&&engine.indexOf(provider+'-api')===0){
          status.textContent='Cloud request succeeded: answer generated by '+answeringLabel(request.model)+'.';
          status.className='v241-api-status ok';
        }else if(status){
          status.textContent='Cloud generation not confirmed for this question; check the answer or API error.';
          status.className='v241-api-status pending';
        }
      }
      if(request)manager.finish(request);
      speakAnswer(ans);
    })();
    if(request)manager.bind(request,task);
    return task;
  }
  // Model selection reuses the last *submitted* question, never a partial live transcript.
  window.VQ_QA_REASK_CURRENT=function(question,source){
    return submitQuestion({force:true,question:question,source:source});
  };
  function speakAnswer(ans){
    try{ if(synth) synth.cancel(); }catch(_){ }
    stopMouthLoop(true);
    answerText = String(ans || '');
    answerIndex = 0;
    if(!synth || !clean(ans)) return;
    const u = new SpeechSynthesisUtterance(String(ans));
    const v = selectedVoice();
    if(v) u.voice = v;
    u.lang = String(window.VQ_LAST_DETECTED_LANGUAGE || (window.VQ_detectLanguage ? window.VQ_detectLanguage(String(ans||'')) : 'en-US'));
    u.rate = rate();
    u.pitch = pitch();
    answerUtterance = u;
    u.onstart = () => { answerText = String(ans || ''); answerIndex = 0; startMouthLoop('answer'); setStatus('AI teacher is answering; the lecture remains paused.'); };
    u.onboundary = (e) => { if(e && typeof e.charIndex === 'number' && e.charIndex >= 0) answerIndex = e.charIndex; };
    u.onend = () => { stopMouthLoop(true); if(lectureRunning && lecturePaused){ setLectureControls(true, true); setStatus('Answer completed. Select “Resume” to continue the lecture.'); }else{ setLectureControls(false, false); setStatus('Answer completed.'); } };
    u.onerror = () => { stopMouthLoop(true); if(lectureRunning && lecturePaused){ setLectureControls(true, true); setStatus('Answer audio stopped. Select “Resume” to continue the lecture.'); }else{ setLectureControls(false, false); setStatus('Answer audio stopped.'); } };
    try{ synth.speak(u); }catch(_){ }
  }

  // v207 first-click microphone fix: make the first Start Voice Question click own the
  // complete microphone startup lifecycle. UI changes immediately while getUserMedia
  // is resolving, stale recognizers are cleared, and Stop can safely cancel a pending start.
  let voiceStartToken = 0;
  let voiceStartPending = false;
  let voiceWarmStream = null;
  let voiceWarmPromise = null;

  function voiceAudioConstraints(){
    return {audio:{echoCancellation:true, noiseSuppression:true, autoGainControl:true}};
  }

  function streamIsLive(stream){
    try{return !!(stream && stream.getAudioTracks && stream.getAudioTracks().some(t=>t.readyState==='live'));}
    catch(_){return false;}
  }

  function stopWarmVoiceStream(){
    if(voiceWarmStream){ try{voiceWarmStream.getTracks().forEach(t=>t.stop());}catch(_){ } }
    voiceWarmStream=null;
  }

  function prewarmVoiceMicrophone(){
    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return Promise.resolve(null);
    if(streamIsLive(voiceWarmStream)) return Promise.resolve(voiceWarmStream);
    if(voiceWarmPromise) return voiceWarmPromise;
    voiceWarmPromise=navigator.mediaDevices.getUserMedia(voiceAudioConstraints()).then(stream=>{
      voiceWarmStream=stream;
      return stream;
    }).catch(()=>null).finally(()=>{ voiceWarmPromise=null; });
    return voiceWarmPromise;
  }

  async function acquireVoiceStream(){
    if(streamIsLive(voiceWarmStream)){ const s=voiceWarmStream; voiceWarmStream=null; return s; }
    if(voiceWarmPromise){
      const pending=await voiceWarmPromise;
      if(streamIsLive(pending)){ if(voiceWarmStream===pending) voiceWarmStream=null; return pending; }
    }
    const s=await navigator.mediaDevices.getUserMedia(voiceAudioConstraints());
    return s;
  }

  function prewarmIfPermissionAlreadyGranted(){
    try{
      if(!navigator.permissions || !navigator.permissions.query) return;
      navigator.permissions.query({name:'microphone'}).then(status=>{
        if(status && status.state==='granted') prewarmVoiceMicrophone();
      }).catch(()=>{});
    }catch(_){ }
  }

  function voiceLanguageHint(){
    // "Auto" must mean acoustic language detection. Browser/UI locale is NOT a
    // speech-language hint: an English Chrome/Windows UI must not bias Mandarin
    // speech toward English. Only an explicit user selection or a previously
    // successful server-STT language is allowed as a soft hint.
    try{
      const sel=$('recognitionLangSelect');
      const explicit=sel && String(sel.value||'').trim();
      if(explicit && !/^auto/i.test(explicit)) return explicit;
    }catch(_){ }
    try{
      const last=localStorage.getItem('VQ_STT_LANGUAGE')||'';
      if(last) return last;
    }catch(_){ }
    return '';
  }

  function browserFallbackLanguage(){
    try{
      const sel=$('recognitionLangSelect');
      const explicit=sel && String(sel.value||'').trim();
      if(explicit && !/^auto/i.test(explicit)) return explicit;
    }catch(_){ }
    try{
      const last=localStorage.getItem('VQ_STT_LANGUAGE')||localStorage.getItem('VQ_LAST_INPUT_LANG')||'';
      if(last) return last;
    }catch(_){ }
    // Web Speech API itself cannot reliably auto-detect. This is only an emergency
    // fallback; the primary MediaRecorder -> server Whisper path remains multilingual.
    return 'zh-TW';
  }


  // R9 speech-to-text route. The production gateway sends /api/kb/* to the KB
  // backend, whose /api/stt reaches the local Faster-Whisper service; the
  // reference backend has no /api/reference/stt route (HTTP 404). Try the
  // working route first and keep /api/reference/stt for gateways that map it.
  const VQ_STT_ROUTES=['/api/kb/stt','/api/reference/stt'];
  let vqSttRoute=0;
  async function postVoiceStt(body,contentType,signal){
    let lastError=null;
    for(let i=0;i<VQ_STT_ROUTES.length;i++){
      const at=(vqSttRoute+i)%VQ_STT_ROUTES.length;
      const options={method:'POST',headers:{'Content-Type':contentType,'X-VQ-Language-Hint':voiceLanguageHint()},body,cache:'no-store'};
      if(signal)options.signal=signal;
      // Both routes use the same origin, so a network failure is not retried.
      const response=await fetch(VQ_STT_ROUTES[at],options);
      // 404/405: route not served here; 502-504: that backend is down or busy. Try the other route.
      if([404,405,502,503,504].includes(response.status)){lastError=new Error('HTTP '+response.status);continue;}
      if(!response.ok)throw new Error('HTTP '+response.status);
      vqSttRoute=at;
      return await response.json();
    }
    throw lastError||new Error('Speech recognition service unavailable');
  }

  function joinVoiceText(before,after){
    const a=clean(before),b=clean(after);if(!a)return b;if(!b)return a;
    const gap=/[㐀-鿿]$/.test(a) && /^[㐀-鿿，。！？、]/.test(b)?'':' ';
    return clean(a+gap+b);
  }

  // R9 instant live text: the browser speech recognizer shows words while the
  // student speaks. It only previews text: it never submits, and it is restarted
  // when the browser ends a recognition session, until the student's Stop.
  // The complete MediaRecorder audio is still transcribed by Faster-Whisper.
  // Finished phrases are kept in rec.__vqCommitted, which server Whisper
  // windows also extend, so neither source can erase the other's words.
  const VQ_WEB_SPEECH_FATAL=/^(not-allowed|service-not-allowed|language-not-supported|audio-capture|network)$/;
  // A browser recognizer that stays silent this long while the student keeps
  // speaking has stalled: it is restarted and Whisper fills in the words.
  const VQ_WEB_SPEECH_STALL_MS=5000;
  function showVoiceText(rec,qBox,interim){
    displayLiveVoiceText(rec,qBox,joinVoiceText(rec.__vqCommitted||'',interim||''));
  }
  function commitVoiceText(rec,text){
    const t=clean(text);if(t)rec.__vqCommitted=appendVoiceSegment(rec.__vqCommitted||'',t);
  }
  function startWebSpeechPreview(rec,qBox){
    const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
    if(!rec || rec.__vqWebSpeech)return;
    if(!SR){rec.__vqWebSpeechFailed=true;return;}
    const ws={closed:false,sr:null,startedAt:0,quickEnds:0,fatal:'',finalCount:0,interim:'',lastResultAt:Date.now()};
    rec.__vqWebSpeech=ws;rec.__vqWebSpeechFailed=false;
    const active=()=>rec.__vqWebSpeech===ws && !ws.closed && !rec.__vqCancelled &&
      !rec.__vqFinalizing && window.__v105_active_recognition===rec;
    const commitInterim=()=>{commitVoiceText(rec,ws.interim);ws.interim='';ws.finalCount=0;};
    const launch=()=>{
      if(!active() || ws.sr)return;
      let sr;
      try{sr=new SR();}catch(_){rec.__vqWebSpeechFailed=true;return;}
      sr.__vqManualVoiceOwner=true;
      sr.lang=browserFallbackLanguage();sr.interimResults=true;sr.continuous=true;
      ws.sr=sr;ws.finalCount=0;ws.interim='';ws.startedAt=Date.now();
      sr.onresult=ev=>{
        if(!active() || ws.sr!==sr)return;
        const results=ev.results||[];let interim='';
        for(let i=ws.finalCount;i<results.length;i++){
          const text=(results[i] && results[i][0] && results[i][0].transcript)||'';
          if(results[i] && results[i].isFinal && i===ws.finalCount){commitVoiceText(rec,text);ws.finalCount=i+1;}
          else interim=joinVoiceText(interim,text);
        }
        ws.interim=clean(interim);
        ws.quickEnds=0;ws.lastResultAt=Date.now();rec.__vqWebSpeechHeard=true;
        if(rec.__vqOnBrowserText)rec.__vqOnBrowserText();
        showVoiceText(rec,qBox,ws.interim);
      };
      sr.onerror=ev=>{const code=String((ev&&ev.error)||'');if(VQ_WEB_SPEECH_FATAL.test(code))ws.fatal=code;};
      sr.onend=()=>{
        if(ws.sr===sr)ws.sr=null;
        commitInterim();
        if(!active())return;
        if(ws.fatal){rec.__vqWebSpeechFailed=true;return;}
        // Browsers end continuous recognition after silence or about a minute.
        // Start a new session while the student is still recording.
        ws.quickEnds=(Date.now()-ws.startedAt<1500)?ws.quickEnds+1:0;
        if(ws.quickEnds>=5){rec.__vqWebSpeechFailed=true;return;}
        setTimeout(launch,ws.quickEnds?600:150);
      };
      try{sr.start();}catch(_){if(ws.sr===sr)ws.sr=null;rec.__vqWebSpeechFailed=true;}
    };
    // A stalled recognizer (no end event, no results) is replaced by a new one.
    ws.restart=()=>{
      const sr=ws.sr;ws.sr=null;commitInterim();showVoiceText(rec,qBox,'');
      if(sr){
        try{sr.onresult=null;sr.onerror=null;sr.onend=null;}catch(_){ }
        try{if(sr.abort)sr.abort();else if(sr.stop)sr.stop();}catch(_){ }
      }
      ws.lastResultAt=Date.now();
      setTimeout(launch,150);
    };
    launch();
  }

  function stopWebSpeechPreview(rec){
    const ws=rec && rec.__vqWebSpeech;if(!ws)return;
    rec.__vqWebSpeech=null;ws.closed=true;
    const sr=ws.sr;ws.sr=null;if(!sr)return;
    try{sr.onresult=null;sr.onerror=null;sr.onend=null;}catch(_){ }
    try{if(sr.abort)sr.abort();else if(sr.stop)sr.stop();}catch(_){ }
  }

  // Manual voice input: audio capture lasts until the student's explicit Stop.
  // Live PCM windows use the existing local Whisper endpoint. No browser speech
  // recognizer reconnect loop, silence-submit timer, counter or extra status UI.
  function startVoiceTranscriptFollow(rec,qBox){
    rec.__vqVoiceQuestionBox=qBox; rec.__vqVoiceFollowTail=true;
    if(!qBox)return;
    rec.__vqVoiceScrollHandler=()=>{
      if(!rec.__vqVoiceScrollPending)rec.__vqVoiceFollowTail=qBox.scrollHeight-qBox.scrollTop-qBox.clientHeight<40;
    };
    qBox.addEventListener('scroll',rec.__vqVoiceScrollHandler,{passive:true});
  }

  function stopVoiceTranscriptFollow(rec){
    if(!rec)return;
    const box=rec.__vqVoiceQuestionBox;
    if(box && rec.__vqVoiceScrollHandler)box.removeEventListener('scroll',rec.__vqVoiceScrollHandler);
    rec.__vqVoiceScrollHandler=null;
  }

  function displayLiveVoiceText(rec,qBox,text){
    const shown=clean(text); if(!shown)return;
    rec.__vqPreviewTranscript=shown;
    if(!qBox)return;
    const follow=rec.__vqVoiceFollowTail!==false;
    if(follow)rec.__vqVoiceScrollPending=true;
    qBox.textContent=shown;
    if(follow){
      const frame=window.requestAnimationFrame||function(fn){setTimeout(fn,0);};
      frame(()=>{
        if(!rec.__vqCancelled)qBox.scrollTop=qBox.scrollHeight;
        frame(()=>{rec.__vqVoiceScrollPending=false;});
      });
    }
  }

  function encodeVoiceWav(samples,rate){
    const bytes=new ArrayBuffer(44+samples.length*2),view=new DataView(bytes);
    const text=(at,value)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
    text(0,'RIFF');view.setUint32(4,36+samples.length*2,true);text(8,'WAVE');
    text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);
    view.setUint16(22,1,true);view.setUint32(24,rate,true);view.setUint32(28,rate*2,true);
    view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,samples.length*2,true);
    for(let i=0;i<samples.length;i++){
      const v=Math.max(-1,Math.min(1,Number.isFinite(samples[i])?samples[i]:0));
      view.setInt16(44+i*2,Math.round(v<0?v*32768:v*32767),true);
    }
    return new Blob([bytes],{type:'audio/wav'});
  }

  function appendVoiceSegment(before,after){
    const a=clean(before),b=clean(after);if(!a)return b;if(!b)return a;
    const join=next=>{
      const rest=next.trimStart();if(!rest)return a;
      const gap=/[\u3400-\u9fff]$/.test(a) && /^[\u3400-\u9fff，。！？、]/.test(rest)?'':' ';
      return clean(a+gap+rest);
    };
    // A short acoustic overlap protects words crossing PCM-window boundaries.
    // Remove only a matching text suffix/prefix; final full-audio STT is authoritative.
    for(let n=Math.min(a.length,b.length,120);n>=2;n--){
      const suffix=a.slice(-n),prefix=b.slice(0,n);
      if(suffix.toLocaleLowerCase()===prefix.toLocaleLowerCase() &&
         (n>=6 || /[\u3400-\u9fff]/.test(suffix)))return join(b.slice(n));
    }
    return join(b);
  }

  function takeVoicePcm(state,count){
    const out=new Float32Array(count);let at=0;
    while(at<count && state.parts.length){
      const part=state.parts[0],n=Math.min(count-at,part.length-state.offset);
      out.set(part.subarray(state.offset,state.offset+n),at);at+=n;state.offset+=n;
      if(state.offset===part.length){state.parts.shift();state.offset=0;}
    }
    state.count-=at;return out.subarray(0,at);
  }

  function stopLiveVoicePreview(rec){
    if(!rec)return;
    stopWebSpeechPreview(rec);
    const p=rec.__vqLivePreview;rec.__vqLivePreview=null;if(!p)return;
    p.closed=true;
    if(p.timer)clearInterval(p.timer);
    if(p.requestTimer)clearTimeout(p.requestTimer);
    try{if(p.controller)p.controller.abort();}catch(_){ }
    try{if(p.node && p.node.port)p.node.port.onmessage=null;}catch(_){ }
    try{if(p.node)p.node.onaudioprocess=null;}catch(_){ }
    for(const node of [p.source,p.node,p.gain])try{if(node)node.disconnect();}catch(_){ }
    try{if(p.ctx && p.ctx.state!=='closed')p.ctx.close();}catch(_){ }
    p.parts=[];p.count=0;p.tail=null;
  }

  async function startLiveVoicePreview(rec,qBox){
    if(!rec || rec.__vqLivePreview)return;
    const AudioCtx=window.AudioContext||window.webkitAudioContext;
    if(!AudioCtx)return; // Complete MediaRecorder audio still reaches final STT.
    const p={closed:false,parts:[],count:0,offset:0,tail:null,busy:false};
    rec.__vqLivePreview=p;
    const active=()=>rec.__vqLivePreview===p && !p.closed && !rec.__vqCancelled &&
      !rec.__vqFinalizing && window.__v105_active_recognition===rec && rec.state==='recording';
    try{
      try{p.ctx=new AudioCtx({sampleRate:16000});}catch(_){p.ctx=new AudioCtx();}
      if(p.ctx.state==='suspended')await p.ctx.resume();
      if(!active()){stopLiveVoicePreview(rec);return;}
      p.rate=p.ctx.sampleRate;
      p.source=p.ctx.createMediaStreamSource(rec.__vqStream);
      p.gain=p.ctx.createGain();p.gain.gain.value=0;
      const receive=frame=>{
        if(!active() || !frame || !frame.length)return;
        let sum=0;for(let i=0;i<frame.length;i++)sum+=frame[i]*frame[i];
        if(Math.sqrt(sum/frame.length)>0.01)p.lastSpeechAt=Date.now();  // the student is speaking
        p.parts.push(new Float32Array(frame));p.count+=frame.length;
      };
      // Browser words cover the audio so far: Whisper only needs what comes after.
      rec.__vqOnBrowserText=()=>{p.parts=[];p.count=0;p.offset=0;p.tail=null;};
      if(p.ctx.audioWorklet && window.AudioWorkletNode && window.URL){
        const code=`class VoiceCapture extends AudioWorkletProcessor{
          constructor(){super();this.buf=new Float32Array(4096);this.at=0;}
          process(inputs){const channels=inputs[0]||[];if(channels.length){
            for(let i=0;i<channels[0].length;i++){let v=0;for(const c of channels)v+=c[i]||0;
              this.buf[this.at++]=v/channels.length;
              if(this.at===this.buf.length){const b=this.buf;this.port.postMessage(b,[b.buffer]);this.buf=new Float32Array(4096);this.at=0;}
            }
          }return true;}
        }registerProcessor('vq-manual-voice',VoiceCapture);`;
        const url=window.URL.createObjectURL(new Blob([code],{type:'application/javascript'}));
        try{
          await p.ctx.audioWorklet.addModule(url);
          if(active()){
            p.node=new window.AudioWorkletNode(p.ctx,'vq-manual-voice',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1]});
            p.node.port.onmessage=e=>receive(e.data);
          }
        }catch(_){ /* Older browsers can use the local PCM processor below. */ }
        finally{window.URL.revokeObjectURL(url);}
      }
      if(!active()){stopLiveVoicePreview(rec);return;}
      if(!p.node){
        p.node=p.ctx.createScriptProcessor(4096,1,1);
        p.node.onaudioprocess=e=>receive(e.inputBuffer.getChannelData(0));
      }
      p.source.connect(p.node);p.node.connect(p.gain);p.gain.connect(p.ctx.destination);
      const pump=async()=>{
        if(!active() || p.busy)return;
        const ws=rec.__vqWebSpeech;let gap=false;
        if(ws && rec.__vqWebSpeechHeard && !rec.__vqWebSpeechFailed){
          // Browser live text has produced words; keep server STT free for the
          // final audio. Until then (or after it fails) Whisper windows supply text.
          const stalled=(p.lastSpeechAt||0)-ws.lastResultAt>VQ_WEB_SPEECH_STALL_MS &&
            Date.now()-ws.lastResultAt>VQ_WEB_SPEECH_STALL_MS;
          if(!stalled){
            if(p.count>p.rate*12)takeVoicePcm(p,p.count-Math.round(p.rate*12));  // only the newest audio
            return;
          }
          // The student kept speaking but the browser stopped producing text:
          // restart it and let Whisper transcribe what it missed.
          rec.__vqWebSpeechHeard=false;
          if(ws.restart)ws.restart();
          gap=true;
        }
        if(p.count<p.rate*(gap?1:4))return;
        const next=takeVoicePcm(p,Math.min(p.count,Math.round(p.rate*12)));
        const tail=p.tail||new Float32Array(0),audio=new Float32Array(tail.length+next.length);
        audio.set(tail);audio.set(next,tail.length);
        p.tail=audio.slice(-Math.round(p.rate*.75));
        if(!audio.some(x=>Math.abs(x)>0.00001))return;
        p.busy=true;
        p.controller=window.AbortController?new window.AbortController():null;
        if(p.controller)p.requestTimer=setTimeout(()=>p.controller&&p.controller.abort(),45000);
        try{
          const data=await postVoiceStt(encodeVoiceWav(audio,p.rate),'audio/wav',p.controller?p.controller.signal:null);
          if(active() && clean(data.text||'')){
            commitVoiceText(rec,data.text);
            const browser=rec.__vqWebSpeech;
            showVoiceText(rec,qBox,browser?browser.interim:'');
          }
        }catch(_){ /* Preserve full recording; only the user's Stop submits a question. */ }
        finally{
          if(p.requestTimer)clearTimeout(p.requestTimer);
          p.requestTimer=null;p.controller=null;p.busy=false;
        }
      };
      p.timer=setInterval(pump,1000);
    }catch(_){if(rec.__vqLivePreview===p)stopLiveVoicePreview(rec);}
  }

  function resetVoiceQuestionControls(clearListeningText){
    const qBox=$('studentQuestion'), ask=$('askBtn'), stop=$('stopAskBtn');
    if(ask) ask.disabled=false;
    if(stop) stop.disabled=true;
    if(qBox){
      qBox.classList.remove('listening-mode');
      const tx=clean(qBox.textContent);
      if(clearListeningText && /^(Starting microphone|Listening|Preparing microphone)/i.test(tx)) qBox.textContent='';
    }
  }

  function clearStaleVoiceRecognition(){
    const stale=window.__v105_active_recognition;
    window.__v105_active_recognition=null;
    if(!stale) return;
    stale.__vqCancelled=true;
    stale.__vqFinalizing=true;
    stopVoiceTranscriptFollow(stale);
    try{ stopLiveVoicePreview(stale); }catch(_){ }
    try{
      if(stale.__vqStream && stale.__vqStream.getTracks) stale.__vqStream.getTracks().forEach(t=>t.stop());
    }catch(_){ }
    try{
      if(stale.__vqServerAudio){ if(stale.state && stale.state!=='inactive') stale.stop(); }
      else if(stale.abort) stale.abort();
      else if(stale.stop) stale.stop();
    }catch(_){ }
  }

  function startBrowserRecognitionFallback(startToken){
    if(startToken != null && startToken!==voiceStartToken)return;
    const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
    const qBox=$('studentQuestion'),ask=$('askBtn'),stop=$('stopAskBtn');
    voiceStartPending=false;
    if(!SR){
      resetVoiceQuestionControls(false);
      if(qBox)qBox.textContent='Automatic speech recognition is unavailable. Please type the question.';
      setStatus('Speech recognition is unavailable; please type the question.');return;
    }
    try{
      clearStaleVoiceRecognition();
      const rec=new SR();
      rec.lang=browserFallbackLanguage();rec.interimResults=true;rec.continuous=true;
      rec.__vqBrowserFallback=true;rec.__vqManualVoiceOwner=true;rec.__vqStartToken=startToken;
      window.__v105_active_recognition=rec;
      if(ask)ask.disabled=true;if(stop)stop.disabled=false;
      if(qBox){qBox.classList.add('listening-mode');qBox.textContent='Listening...';}
      startVoiceTranscriptFollow(rec,qBox);
      rec.__vqFinishBrowserCapture=()=>{
        if(!rec.__vqManualStopRequested || rec.__vqSubmitting || rec.__vqCancelled ||
           rec.__vqStartToken!==voiceStartToken)return;
        rec.__vqSubmitting=true;
        if(window.__v105_active_recognition===rec)window.__v105_active_recognition=null;
        stopVoiceTranscriptFollow(rec);resetVoiceQuestionControls(false);
        const text=clean(rec.__vqPreviewTranscript||'');
        if(!text){setStatus('No clear speech was detected. Please try again.');return;}
        if(qBox)qBox.textContent=text;
        window.VQ_NEXT_INPUT_SOURCE='voice';submitQuestion();
      };
      rec.__vqSessionBase='';rec.__vqSessionStartedAt=Date.now();rec.__vqQuickEnds=0;
      rec.onresult=ev=>{
        if(window.__v105_active_recognition!==rec || rec.__vqCancelled)return;
        let session='';for(let i=0;i<ev.results.length;i++)session=joinVoiceText(session,ev.results[i][0].transcript||'');
        rec.__vqQuickEnds=0;
        displayLiveVoiceText(rec,qBox,joinVoiceText(rec.__vqSessionBase,session));
      };
      rec.onerror=ev=>{
        if(rec.__vqCancelled)return;
        const code=String((ev&&ev.error)||'');
        if(VQ_WEB_SPEECH_FATAL.test(code)){
          rec.__vqFatal=code;
          setStatus('Browser speech recognition stopped. Your text is retained; select Stop Recording & Answer to submit it.');
        }
      };
      rec.onend=()=>{
        rec.__vqBrowserListening=false;
        if(rec.__vqCancelled)return;
        if(rec.__vqManualStopRequested){rec.__vqFinishBrowserCapture();return;}
        if(window.__v105_active_recognition!==rec)return;
        rec.__vqQuickEnds=(Date.now()-rec.__vqSessionStartedAt<1500)?rec.__vqQuickEnds+1:0;
        if(rec.__vqFatal || rec.__vqQuickEnds>=5){
          setStatus('Browser speech recognition ended. Select Stop Recording & Answer to submit the captured text.');return;
        }
        // Keep listening until the student selects Stop Recording & Answer.
        setTimeout(()=>{
          if(rec.__vqCancelled || rec.__vqManualStopRequested || window.__v105_active_recognition!==rec)return;
          rec.__vqSessionBase=clean(rec.__vqPreviewTranscript||'');rec.__vqSessionStartedAt=Date.now();
          try{rec.start();}catch(_){ }
        },rec.__vqQuickEnds?600:150);
      };
      rec.onstart=()=>{rec.__vqBrowserListening=true;};
      rec.start();setStatus('Listening — select Stop Recording & Answer when finished.');
    }catch(_){
      clearStaleVoiceRecognition();resetVoiceQuestionControls(false);
      if(qBox)qBox.textContent='Speech recognition could not start. Please type the question.';
    }
  }

  async function startRecognition(){
    pauseLectureForQuestion();
    const qBox=$('studentQuestion'), ask=$('askBtn'), stop=$('stopAskBtn');
    const myToken=++voiceStartToken;
    voiceStartPending=true;

    // This must happen before awaiting microphone permission/device startup so the
    // first click is visibly and functionally acknowledged immediately.
    clearStaleVoiceRecognition();
    if(ask) ask.disabled=true;
    if(stop) stop.disabled=true;
    if(qBox){ qBox.classList.add('listening-mode'); qBox.textContent='Starting microphone — please wait...'; }
    setStatus('Starting microphone...');

    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder){
      startBrowserRecognitionFallback(myToken);
      return;
    }
    let stream=null;
    try{
      stream=await acquireVoiceStream();
      if(!streamIsLive(stream)) throw new Error('Microphone stream is not active');
      // Stop may have been pressed while permission/device startup was pending.
      if(myToken !== voiceStartToken){
        try{stream.getTracks().forEach(t=>t.stop());}catch(_){ }
        return;
      }
      const preferred=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus'];
      let mime=''; for(const m of preferred){ if(MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)){mime=m;break;} }
      const rec=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);
      rec.__vqServerAudio=true; rec.__vqChunks=[]; rec.__vqStream=stream; rec.__vqSubmitting=false; rec.__vqStartToken=myToken; rec.__vqPreviewTranscript=''; rec.__vqCommitted=''; rec.__vqManualStopRequested=false;
      window.__v105_active_recognition=rec;
      rec.ondataavailable=e=>{if(e.data&&e.data.size)rec.__vqChunks.push(e.data);};
      rec.onstart=()=>{
        if(myToken !== voiceStartToken) return;
        voiceStartPending=false;
        if(ask)ask.disabled=true;if(stop)stop.disabled=false;
        if(qBox){qBox.classList.add('listening-mode');qBox.textContent='Listening — speak now...';}
        setStatus('Listening — select Stop Recording & Answer when finished.');
        startVoiceTranscriptFollow(rec,qBox);
        startWebSpeechPreview(rec,qBox);
        startLiveVoicePreview(rec,qBox);
      };
      rec.onerror=()=>{
        if(rec.__vqCancelled)return;
        voiceStartPending=false;
        stopVoiceTranscriptFollow(rec);
        stopLiveVoicePreview(rec);
        try{stream.getTracks().forEach(t=>t.stop());}catch(_){ }
        if(qBox)qBox.classList.remove('listening-mode');
        setStatus('Microphone recording failed. Select Stop Recording & Answer to process the captured audio.');
      };
      rec.onstop=async()=>{
        if(rec.__vqCancelled){stopLiveVoicePreview(rec);stopVoiceTranscriptFollow(rec);return;}
        if(!rec.__vqManualStopRequested){
          stopLiveVoicePreview(rec);stopVoiceTranscriptFollow(rec);
          setStatus('Microphone recording ended. Select Stop Recording & Answer to process the captured audio.');
          return;
        }
        if(rec.__vqSubmitting)return; rec.__vqSubmitting=true;
        rec.__vqFinalizing=true;
        stopLiveVoicePreview(rec);
        stopVoiceTranscriptFollow(rec);
        voiceStartPending=false;
        if(window.__v105_active_recognition===rec) window.__v105_active_recognition=null;
        try{ rec.__vqStream.getTracks().forEach(t=>t.stop()); }catch(_){ }
        if(ask)ask.disabled=false;if(stop)stop.disabled=true;if(qBox)qBox.classList.remove('listening-mode');
        // Only explicit Stop submits. Preserve the preview while the complete
        // recorded audio is transcribed; a full-audio result remains authoritative.
        const currentCapture=()=>!rec.__vqCancelled && rec.__vqStartToken===voiceStartToken;
        const previewText=clean(rec.__vqPreviewTranscript || '');
        const blob=new Blob(rec.__vqChunks,{type:rec.mimeType||'audio/webm'});
        if(qBox && previewText) qBox.textContent=previewText;
        setStatus('Transcribing recorded audio; the question will be submitted automatically...');

        const submitCapturedPreview=async(reason)=>{
          if(!currentCapture())return false;
          const fallbackText=clean(previewText || rec.__vqPreviewTranscript || '');
          if(!fallbackText) return false;
          if(qBox) qBox.textContent=fallbackText;
          try{
            const guessed=(window.VQ_detectLanguage ? window.VQ_detectLanguage(fallbackText) : '');
            if(guessed) window.VQ_LAST_DETECTED_LANGUAGE=guessed;
          }catch(_){ }
          window.VQ_NEXT_INPUT_SOURCE='voice';
          setStatus(reason || 'Using the captured speech transcript and preparing the answer...');
          await submitQuestion();
          return true;
        };

        if(!blob.size){
          if(await submitCapturedPreview('Recorded audio was empty; using the captured live transcript to answer.')) return;
          if(!currentCapture())return;
          if(qBox)qBox.textContent='No speech was recorded. Please try again.';
          return;
        }
        // A stalled transcription must not leave Stop Recording & Answer waiting indefinitely.
        // Prefer the full recording, but allow an available live transcript as a bounded fallback.
        const STTAbort=window.AbortController||(typeof AbortController!=='undefined'?AbortController:null);
        const sttController=STTAbort?new STTAbort():null;
        const sttLimit=previewText?10000:40000,sttStarted=Date.now();
        const sttDeadline=setTimeout(()=>{if(sttController)sttController.abort();},sttLimit);
        // Show progress so a slow transcription never looks like a frozen button.
        const sttTicker=setInterval(()=>{if(!currentCapture())return;const s=Math.round((Date.now()-sttStarted)/1000);
          setStatus('Transcribing your recording… '+s+' s — the answer starts automatically'+(previewText?' (at most '+Math.round(sttLimit/1000)+' s, then the live transcript is used).':'.'));},1000);
        try{
          const d=await postVoiceStt(blob,blob.type||'audio/webm',sttController?sttController.signal:undefined); const text=clean(d.text||'');
          if(!currentCapture())return;
          if(text){
            if(qBox)qBox.textContent=text;
            if(d.language){
              window.VQ_LAST_STT_LANGUAGE=String(d.language);
              window.VQ_LAST_DETECTED_LANGUAGE=String(d.language);
              try{localStorage.setItem('VQ_STT_LANGUAGE',String(d.language));}catch(_){ }
            }
            window.VQ_NEXT_INPUT_SOURCE='voice';
            await submitQuestion();
            return;
          }
          if(await submitCapturedPreview('Server transcription was empty; using the captured live transcript to answer.')) return;
          if(!currentCapture())return;
          if(qBox)qBox.textContent='No clear speech was detected. Please try again.';
        }catch(err){
          if(!currentCapture())return;
          if(await submitCapturedPreview('Server transcription is unavailable; using the captured live transcript to answer.')) return;
          if(!currentCapture())return;
          if(qBox)qBox.textContent='Server speech recognition is temporarily unavailable. Please try again or type the question.';
          setStatus('Server speech recognition failed and no usable live transcript was available.');
        }finally{clearTimeout(sttDeadline);clearInterval(sttTicker);}
      };
      rec.start(250);
    }catch(_){
      voiceStartPending=false;
      try{ if(stream) stream.getTracks().forEach(t=>t.stop()); }catch(__){ }
      if(myToken !== voiceStartToken) return;
      startBrowserRecognitionFallback(myToken);
    }
  }

  function stopRecognitionAndSubmit(){
    const hadPending=voiceStartPending;
    if(hadPending){
      setStatus('Microphone is still starting. Wait for “Listening...” before selecting Stop.');
      return;
    }
    const rec=window.__v105_active_recognition;
    if(!rec){
      resetVoiceQuestionControls(true);
      return;
    }
    if(rec.__vqManualStopRequested)return;
    rec.__vqManualStopRequested=true;rec.__vqFinalizing=true;
    try{
      if(rec.__vqServerAudio){
        stopLiveVoicePreview(rec);
        if(rec.state!=='inactive')rec.stop();else rec.onstop();
      }else{
        if(rec.__vqBrowserListening)rec.stop();else rec.__vqFinishBrowserCapture();
        setTimeout(()=>rec.__vqFinishBrowserCapture(),1500);
      }
    }catch(_){ resetVoiceQuestionControls(false); }
  }

  function handleClickCapture(e){
    const t = e.target && e.target.closest ? e.target.closest('button') : null;
    if(!t || !t.id) return;
    const map = {
      lessonTab: () => stopForModeSwitch('lessonTab'),
      caseTab: () => stopForModeSwitch('caseTab'),
      modelTab: () => stopForModeSwitch('modelTab'),
      startLessonBtn: startLectureFromBeginning,
      pauseLessonBtn: pauseLecture,
      resumeLessonBtn: resumeLecture,
      stopLessonBtn: stopLecture,
      repeatBtn: startLectureFromBeginning,
      askBtn: startRecognition,
      stopAskBtn: stopRecognitionAndSubmit,
      submitTypedQuestionBtn: submitQuestion
    };
    if(!map[t.id]||t.disabled) return;
    if(t.id==='startLessonBtn'||t.id==='repeatBtn'){
      const now=Date.now();if(now-(window.__R12_LAST_LECTURE_START__||0)<650){e.preventDefault();e.stopImmediatePropagation();return;}
      window.__R12_LAST_LECTURE_START__=now;
    }
    e.preventDefault();
    e.stopImmediatePropagation();
    map[t.id]();
  }

  function setupQuestionBox(){
    const q = $('studentQuestion');
    if(!q) return;
    q.setAttribute('contenteditable','true');
    q.setAttribute('data-placeholder','Type your question here, or select “Start Voice Question” to use speech input.');
    if((q.textContent || '').trim() === 'The student question will appear here.') q.textContent = '';
    if(!q.dataset.v109Bound){
      q.dataset.v109Bound = '1';
      // v186: do not pause merely on focus; mobile scrolling may focus this box.
      // Pause when actual editing begins instead.
      q.addEventListener('beforeinput', () => { if(lectureRunning && !lecturePaused) pauseLectureForQuestion(); }, true);
      q.addEventListener('input', () => { if(lectureRunning && !lecturePaused) pauseLectureForQuestion(); }, true);
    }
  }

  function bindControls(){
    if(installed) return;
    installed = true;
    injectStyles();
    document.addEventListener('click', handleClickCapture, true);
    setupQuestionBox();
    const voiceStartBtn=$('askBtn');
    if(voiceStartBtn && !voiceStartBtn.dataset.vq207PrewarmBound){
      voiceStartBtn.dataset.vq207PrewarmBound='1';
      voiceStartBtn.addEventListener('pointerdown',()=>{prewarmVoiceMicrophone();},{passive:true});
      voiceStartBtn.addEventListener('touchstart',()=>{prewarmVoiceMicrophone();},{passive:true});
    }
    prewarmIfPermissionAlreadyGranted();

    const sel = $('lessonSelect');
    if(sel && !sel.dataset.v109Bound){
      sel.dataset.v109Bound = '1';
      sel.addEventListener('change', () => {
        stopLecture();
        const lesson = getLessonScript();
        const box = $('lessonText');
        if(box){
          box.classList.remove('vq109-script-box');
          box.textContent = 'Selected: ' + lesson.title + '. Select “Start Lecture”.';
        }
      }, true);
    }

    setLectureControls(false, false);
    if(controlTimer) clearInterval(controlTimer);
    controlTimer = setInterval(enforceControls, 120);
    stopMouthLoop(true);
    setStatus('System ready.');
  }

  window.VQ_V110_stopAllTeachingMedia = stopForModeSwitch;

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindControls);
  else bindControls();
})();
