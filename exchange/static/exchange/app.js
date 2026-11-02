(() => {
  const $ = id => document.getElementById(id);
  // Leave room for the sticky header when a new step, notice or focused control scrolls into view.
  // Measure it because its mobile layout and active-access label can change its height.
  const judgeHeader=document.querySelector('.judge-header');
  if(judgeHeader){
    const updateScrollOffset=()=>{
      document.documentElement.style.scrollPaddingTop=(Math.ceil(judgeHeader.getBoundingClientRect().height)+16)+'px';
    };
    updateScrollOffset();
    if('ResizeObserver' in window)new ResizeObserver(updateScrollOffset).observe(judgeHeader);
    else window.addEventListener('resize',updateScrollOffset);
  }
  let sessionId = null, choices = [], chosen = null, quoteId = null, flowPanel = 'reason', currentLine = null, policy = null;
  let orderItems = [], previewMode = 'recorded', displayedPreviewJobs = {}, previewRevision = 0;
  const BEAUTY = ['lip','foundation','blush','nail','hair'];
  const isBeauty = () => currentLine && BEAUTY.includes(currentLine.category);
  // Shoes and bags share the jewellery panel: a size change, and a colour family where jewellery has a metal.
  const JEWELLERY = ['earrings','necklace','shoes','bag'];
  const isJewellery = () => currentLine && JEWELLERY.includes(currentLine.category);
  const isNecklace = () => currentLine && currentLine.category === 'necklace';
  const isShoes = () => currentLine && currentLine.category === 'shoes';
  const isBag = () => currentLine && currentLine.category === 'bag';
  const isAccessory = () => isShoes() || isBag();
  // Item categories with recorded pilot results; recorded previews are offered only for these. Read when
  // needed, because the page lists them after this script.
  const recordedCategories = () => JSON.parse(($('recorded-categories') || {}).textContent || '[]');
  // A product photo where the catalog has one, otherwise a labelled swatch of the shade.
  function visualFor(item){
    if(item.image_url){const img=document.createElement('img');img.src=item.image_url;img.alt=item.image_alt||('Image of '+item.title);return img;}
    const swatch=document.createElement('div');
    if(item.shade_hex){swatch.className='swatch';swatch.style.background=item.shade_hex;
      swatch.setAttribute('role','img');swatch.setAttribute('aria-label','Swatch of '+item.shade_name+', '+item.product_line);
      // The name sits on its own light chip, so it stays readable on any shade.
      const name=document.createElement('span');name.className='swatch-name';name.textContent=item.shade_name;
      swatch.append(name);return swatch;}
    swatch.className='product-art';swatch.textContent='◇';swatch.setAttribute('aria-label','Source product image pending');return swatch;
  }
  const money = cents => 'USD ' + (cents / 100).toFixed(2);
  const motion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  // Focus moves to the new step's heading, so keyboard and screen reader users land where the page went,
  // even when the button they used was in a step that is now hidden.
  const show = (id, focus=true) => {
    const panel = $(id); panel.hidden = false; if(id !== 'return') flowPanel = id;
    const heading = panel.querySelector('h2');
    if(focus && heading){ heading.tabIndex = -1; heading.focus({preventScroll:true}); }
    panel.scrollIntoView({behavior:motion(),block:'start'});
  };
  // Problems scroll into view; informational notes after showing results do not pull the page away.
  const notice = (message, scroll=true) => {
    $('notice').textContent = message; if(message && scroll) $('notice').scrollIntoView({behavior:motion()});
  };
  async function api(path, method='GET', payload=null, extra={}) {
    const res = await fetch(path, {method, credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-CSRFToken':window.CSRF_TOKEN,...extra},
      body: payload === null ? null : JSON.stringify(payload)});
    const data = await res.json();
    if(!res.ok){
      const error = new Error(data.error?.message || 'Request failed.');
      error.code = data.error?.code; error.status = res.status;
      error.concernToken=data.error?.concern_token;error.previewJobId=data.error?.preview_job_id;throw error;
    }
    return data;
  }
  const FLOW = ['items','order','reason','sizes','support','shadePrefs','jewelPrefs','prefs','copilot','photo','choices','look','review','done'];
  $('returnBtn').onclick = () => { if(!$('return').hidden) return; show('return'); };
  $('backBtn').onclick = () => { $('return').hidden = true; show(sessionId ? flowPanel : 'start'); };
  $('endReturn').onclick = async () => {
    try {
      const message = sessionId ? (await api('/api/sessions/'+sessionId+'/return','POST',{})).message
        : 'This demo has not started a refund or return.';
      FLOW.forEach(id => { if($(id)) $(id).hidden = true; });
      $('start').hidden = true; $('endReturn').hidden = true; $('backBtn').hidden = true;
      $('returnDone').textContent = message; notice(''); show('return');
    } catch(e){ notice(e.message); }
  };
  async function startOrder(){
    try {
      // A store links here with its own order's return token; the demo order is the default.
      const token = new URLSearchParams(location.search).get('order') || 'DEMO-ORDER-001';
      const data = await api('/api/sessions','POST',{order_token:token});
      sessionId = data.session_id; $('start').hidden = true; policy = data.policy; orderItems = data.items || [];
      if(data.items.length > 1){ renderItems(data.items, data.order.line_id); show('items'); }
      else { renderOrder(data.order, data.reasons); show('reason'); }
      notice('');
    } catch(e){ notice(e.message); }
  }
  $('startBtn').onclick = startOrder;
  function renderOrder(line, reasons){
    currentLine = line;
    $('orderTitle').textContent = line.title;
    const until = new Date(policy.window_ends_at).toLocaleDateString(undefined,{day:'numeric',month:'long',year:'numeric'});
    $('orderDetails').textContent = line.fictional_order_id + (line.size_label ? ' · Size ' + line.size_label : '') +
      (line.opened ? ' · Opened' : '') + ' · Paid ' + money(line.paid_minor) + ' · Eligible credit ' +
      money(line.eligible_credit_minor) + (policy.exchange_allowed ? ' · Exchange or return until ' + until : ' · The exchange window has closed');
    if(line.image_url){
      $('orderImage').src=line.image_url;$('orderImage').alt=line.image_alt;
      $('orderImageLabel').textContent=line.image_label;$('orderFigure').hidden=false;$('orderSwatch')?.remove();
    } else {
      $('orderFigure').hidden=true;$('orderSwatch')?.remove();
      if(line.shade_hex){const swatch=visualFor(line);swatch.id='orderSwatch';swatch.classList.add('order-swatch');$('order').append(swatch);}
    }
    $('reasonOptions').replaceChildren(...reasons.map(r=>{
      const label=document.createElement('label');label.className='check';
      const input=document.createElement('input');input.type='radio';input.name='reason';input.value=r.code;
      label.append(input,' '+r.label);return label;}));
    $('openedNote').hidden = !(line.opened && policy.opened_same_line);
    $('order').hidden = false;
  }
  function renderItems(items, current){
    $('itemsIntro').textContent = items[0].fictional_order_id + ' · ' + items.length + ' items. Pick the one you want to send back.';
    $('itemList').replaceChildren(...items.map(item=>{
      const card=document.createElement('article');card.className='card item-card';
      card.append(visualFor(item));
      const title=document.createElement('h3');title.textContent=item.title;
      const facts=document.createElement('p');facts.className='small';
      facts.textContent=item.category_name+(item.opened?' · opened':'')+' · paid '+money(item.paid_minor);
      const button=document.createElement('button');button.type='button';button.className='button secondary';
      // Each name starts with the words on the button, so voice control finds it by what it says.
      button.textContent='Return this item';button.setAttribute('aria-label','Return this item: '+item.title);
      button.onclick=()=>chooseItem(item);
      card.append(title,facts,button);return card;}));
  }
  async function chooseItem(item){
    try{
      const data=await api('/api/sessions/'+sessionId+'/item','PUT',{line_id:item.line_id});
      ['reason','sizes','support','shadePrefs','jewelPrefs','prefs','choices','look','photo','review'].forEach(id=>$(id).hidden=true);
      renderOrder(data.order, data.reasons); show('reason'); notice('');
    }catch(e){notice(e.message);}
  }
  // A link such as /?return=lip opens the demo order at that item, so a judge lands on the step that matters.
  const asked = new URLSearchParams(location.search).get('return');
  if(new URLSearchParams(location.search).get('order') && !asked) startOrder();
  if(asked){
    startOrder().then(()=>{const item=orderItems.find(i=>i.category===asked); if(item) chooseItem(item);});
  }
  $('reasonBtn').onclick = async () => {
    const picked = document.querySelector('input[name="reason"]:checked');
    if(!picked) return notice('Choose the main reason first.');
    try {
      const data = await api('/api/sessions/'+sessionId+'/reason','PUT',{reason:picked.value});
      ['sizes','support','shadePrefs','jewelPrefs','prefs','choices','look','photo','review'].forEach(id => $(id).hidden = true);
      choices = []; notice('');
      if(data.next === 'preferences') show('prefs');
      else if(data.next === 'shade_preferences') show('shadePrefs');
      else if(data.next === 'jewellery_preferences'){ setJewelWords(); show('jewelPrefs'); }
      else if(data.next === 'sizes') await loadSizes();
      else if(data.next === 'support'){ $('supportMessage').textContent = data.message; show('support'); }
      else show('return');
    } catch(e){ notice(e.message); }
  };
  $('supportReturn').onclick = () => show('return');
  async function loadSizes(){
    const data = await api('/api/sessions/'+sessionId+'/sizes');
    $('sizesMessage').textContent = data.message + ' You have size ' + data.current_size + '.';
    $('sizeCards').replaceChildren();
    if(!data.sizes.length) $('sizesMessage').textContent += ' No other size is listed for this top.';
    for(const item of data.sizes){
      const card=document.createElement('article');card.className='card';
      if(item.image_url){const img=document.createElement('img');img.src=item.image_url;img.alt=item.image_alt;card.append(img);}
      const title=document.createElement('h3');title.textContent='Size '+item.size_label;card.append(title);
      const note=document.createElement('p');note.textContent=item.note;card.append(note);
      if(item.available){
        const cost=document.createElement('p');cost.className='money';cost.textContent=money(item.top_up_minor)+' extra';card.append(cost);
        const button=document.createElement('button');button.type='button';button.className='button secondary';
        button.textContent='Choose size '+item.size_label;button.setAttribute('aria-label','Choose size '+item.size_label+': '+item.title);
        button.onclick=()=>choose(item,card);card.append(button);
      }
      $('sizeCards').append(card);
    }
    show('sizes');
  }
  let confirmedTopPreferences=null;
  let confirmingTopPreferences=false;
  $('confirmBtn').onclick = async () => {
    if(confirmingTopPreferences) return;
    const raw = $('budget').value;
    if(!/^\d+(\.\d{1,2})?$/.test(raw)) return notice('Enter a nonnegative dollar amount with at most two decimals.');
    const cents = Math.round(Number(raw)*100);
    confirmingTopPreferences=true;
    $('confirmBtn').disabled=true;$('copilotBtn').disabled=true;choicesGuide.disabled=true;
    try {
      const confirmed=await api('/api/sessions/'+sessionId+'/preferences','PUT',{
        quieter_print:$('quieter').checked,sleeve_required:$('sleeve').value,
        color_preferences:$('color').value.split(/,|\bor\b/).map(c=>c.trim().toLowerCase()).filter(Boolean).slice(0,3),
        neckline_preference:$('neckline').value,max_top_up_minor:cents,currency:'USD',note:$('note').value});
      confirmedTopPreferences=confirmed.confirmed_preferences;
      $('copilot').hidden=true;
      if($('tradeoffReview')) $('tradeoffReview').hidden=true;
      await renderCandidates($('noMatch'));
    } catch(e){notice(e.message);}
    finally{confirmingTopPreferences=false;$('confirmBtn').disabled=false;$('copilotBtn').disabled=false;choicesGuide.disabled=false;}
  };
  $('shadeConfirmBtn').onclick = async () => {
    const raw = $('shadeBudget').value;
    if(!/^\d+(\.\d{1,2})?$/.test(raw)) return notice('Enter a nonnegative dollar amount with at most two decimals.');
    if(!$('lightness').value && !$('tone').value && !$('intensity').value) return notice('Choose at least one change to the shade.');
    try {
      await api('/api/sessions/'+sessionId+'/preferences','PUT',{lightness:$('lightness').value,tone:$('tone').value,
        intensity:$('intensity').value,max_top_up_minor:Math.round(Number(raw)*100),currency:'USD',note:$('shadeNote').value});
      await renderCandidates($('shadeNoMatch'));
    } catch(e){notice(e.message);}
  };
  $('jewelConfirmBtn').onclick = async () => {
    const raw = $('jewelBudget').value;
    if(!/^\d+(\.\d{1,2})?$/.test(raw)) return notice('Enter a nonnegative dollar amount with at most two decimals.');
    if(!$('jewelSize').value && !$('jewelMetal').value) return notice('Choose what should change: the size or the metal.');
    try {
      await api('/api/sessions/'+sessionId+'/preferences','PUT',{size:$('jewelSize').value,metal:$('jewelMetal').value,
        max_top_up_minor:Math.round(Number(raw)*100),currency:'USD',note:$('jewelNote').value});
      await renderCandidates($('jewelNoMatch'));
    } catch(e){notice(e.message);}
  };
  async function renderCandidates(noMatchBox){
      const data = await api('/api/sessions/'+sessionId+'/candidates');
      choices = data.candidates; displayedPreviewJobs = {}; previewRevision++; $('cards').replaceChildren();
      $('baselineWrap').hidden = true; $('previewSubject').hidden = true; $('look').hidden = true; $('advice').hidden = true;
      noMatchBox.hidden = true;
      if(!choices.length){
        const lead=document.createElement('p');lead.className='question';lead.textContent=data.message;
        const list=document.createElement('ul');
        for(const x of data.exclusions){const li=document.createElement('li');li.textContent=x.text;list.append(li);}
        noMatchBox.replaceChildren(lead,list);
        if(currentLine.category==='tops') noMatchBox.append(tradeoffExplorer(noMatchBox));
        noMatchBox.hidden=false;$('choices').hidden=true;notice('');
        noMatchBox.scrollIntoView({behavior:motion(),block:'center'});
        return;
      }
      const noun = isBeauty() ? 'shade' : isNecklace() ? 'necklace' : isBag() ? 'bag' : isJewellery() ? 'pair' : 'top';
      for(const item of choices){
        const card = document.createElement('article');card.className='card';card.dataset.sku=item.sku;
        const visual = visualFor(item);
        const frame=document.createElement('div');frame.className='card-visual';
        const source=document.createElement('figure');source.className='visual-source';source.append(visual);
        if(item.image_label){const caption=document.createElement('figcaption');caption.className='source-label';
          caption.textContent=item.image_label;source.append(caption);}
        frame.append(source);card.append(frame);
        const title=document.createElement('h3');title.textContent=item.title;card.append(title);
        if(item.details){const facts=document.createElement('p');facts.className='small';facts.textContent=item.details;card.append(facts);}
        const cost=document.createElement('p');cost.className='money';cost.textContent=money(item.top_up_minor)+' extra';card.append(cost);
        const reasons=document.createElement('p');reasons.textContent=item.reasons.join(' · ');card.append(reasons);
        const button=document.createElement('button');button.type='button';button.className='button';button.textContent='Choose this '+noun;
        button.setAttribute('aria-label','Choose this '+noun+': '+item.title);
        button.onclick=()=>choose(item,card);card.append(button);$('cards').append(card);
      }
      $('choices').querySelector('h2').textContent = isBeauty() ? 'Compare shades that change the way you asked.'
        : isNecklace() ? 'Compare necklaces that change the way you asked.'
        : isShoes() ? 'Compare shoes that change the way you asked.'
        : isBag() ? 'Compare bags that change the way you asked.'
        : isJewellery() ? 'Compare earrings that change the way you asked.' : 'Compare what meets your limits.';
      $('choicesNote').textContent = isBeauty()
        ? 'Shades come from the fictional catalog as swatches. A generated preview cannot prove the exact colour on you; trust the swatch.'
        : isBag() ? 'Bag details come from the fictional catalog. Bag previews are off: YouCam\'s bag try-on dresses the person in a new outfit and setting, so it can\'t show a bag on you fairly. Compare the bags by their details.'
        : isShoes() ? 'Shoe details come from the fictional catalog. A generated preview cannot prove fit, size or feel; check the heel height in the details.'
        : isJewellery() ? (isNecklace() ? 'Necklace' : 'Earring') + ' details come from the fictional catalog. A generated preview cannot prove the exact size, weight or finish; check the length in the details.'
        : 'Source garment details come from the fictional catalog. Generated appearance cannot prove physical fit, exact color, drape or fabric feel.';
      // Bags have no try-on: the bags pilot showed it restyles the whole photo.
      $('previewBtn').hidden = isBag(); $('tryOnNote').hidden = isBag();
      if($('choicesCopilotBtn')) $('choicesCopilotBtn').hidden=currentLine.category!=='tops';
      $('choices').hidden=false;show('choices');notice('');
  }
  function tradeoffExplorer(noMatchBox){
    const baseline={...confirmedTopPreferences};
    const section=document.createElement('section');section.className='tradeoff-explorer';
    const heading=document.createElement('h3');heading.textContent='Could one change help?';
    const intro=document.createElement('p');intro.textContent='Choose one confirmed limit you are willing to revisit. Every other limit, current stock and exchange policy still apply. Exploring changes nothing.';
    const form=document.createElement('form');
    const label=document.createElement('label');label.htmlFor='tradeoffConstraint';label.textContent='Which limit would you revisit?';
    const select=document.createElement('select');select.id='tradeoffConstraint';select.required=true;
    const options=[['','Choose one limit'],['budget','Maximum extra spend']];
    if(baseline.sleeve_required) options.push(['sleeves','Sleeves']);
    if(baseline.quieter_print) options.push(['print','Quieter print or pattern']);
    select.replaceChildren(...options.map(([value,textContent])=>Object.assign(document.createElement('option'),{value,textContent})));
    const button=document.createElement('button');button.type='submit';button.className='button secondary';button.textContent='Explore this limit';
    const feedback=document.createElement('p');feedback.setAttribute('role','status');
    const results=document.createElement('div');
    select.onchange=()=>{results.replaceChildren();feedback.textContent='';};
    form.append(label,select,button);section.append(heading,intro,form,feedback,results);
    form.onsubmit=async event=>{
      event.preventDefault();const constraint=select.value;if(!constraint)return;
      button.disabled=true;results.replaceChildren();feedback.textContent='Checking current catalog possibilities…';
      try{
        const data=await api('/api/sessions/'+sessionId+'/tradeoffs?constraint='+encodeURIComponent(constraint));
        if(select.value!==constraint)return;
        feedback.textContent=data.message;
        for(const plan of data.possibilities){
          const card=document.createElement('article');card.className='tradeoff-card';
          const title=document.createElement('h4');title.textContent=plan.change;
          const note=document.createElement('p');note.textContent=plan.minimum_budget
            ? 'Lowest budget that unlocks an eligible top in the current catalog with your other limits unchanged.'
            : 'A change to this one limit, with your other limits unchanged.';
          const kept=document.createElement('ul');
          for(const fact of plan.kept){const li=document.createElement('li');li.textContent='Keeps: '+fact;kept.append(li);}
          const items=document.createElement('p');items.textContent='Would unlock: '+plan.candidates.map(item=>item.title+' ('+money(item.top_up_minor)+' extra)').join('; ');
          const review=document.createElement('button');review.type='button';review.className='button secondary';review.textContent='Review this change';
          review.onclick=()=>{
            $('budget').value=(baseline.max_top_up_minor/100).toFixed(2);
            $('sleeve').value=baseline.sleeve_required;
            $('quieter').checked=baseline.quieter_print;
            $('color').value=(baseline.color_preferences||[]).join(', ');
            $('neckline').value=baseline.neckline_preference||'';
            $('note').value=baseline.note||'';
            const control=constraint==='budget'?$('budget'):constraint==='sleeves'?$('sleeve'):$('quieter');
            if(constraint==='budget')control.value=(plan.after/100).toFixed(2);
            else if(constraint==='sleeves')control.value=plan.after;
            else control.checked=plan.after;
            let message=$('tradeoffReview');
            if(!message){message=document.createElement('p');message.id='tradeoffReview';message.className='understood';message.setAttribute('role','status');$('confirmBtn').before(message);}
            message.textContent='Proposed change: '+plan.change+'. Other controls are restored to your last confirmation. Review them and choose Confirm preferences. Your confirmed limits still apply until then.';
            message.hidden=false;noMatchBox.hidden=true;control.focus();control.scrollIntoView({behavior:motion(),block:'center'});
          };
          card.append(title,note,kept,items,review);results.append(card);
        }
      }catch(error){feedback.textContent=error.message;}
      finally{button.disabled=false;}
    };
    return section;
  }
  // The jewellery panel's words for a pair of earrings or for one necklace.
  function setJewelWords(){
    const necklace = isNecklace();
    // Earrings and necklaces come in gold or silver; shoes and bags in a neutral or a bright colour.
    const colours = isAccessory();
    $('jewelMetal').replaceChildren(...(colours ? [['','Either colour'],['neutral','Neutral: black, brown, tan, cream'],['bright','Bright: red, yellow, coral, metallic']]
      : [['','Either metal'],['gold','Gold'],['silver','Silver']]).map(([value,label])=>Object.assign(document.createElement('option'),{value,textContent:label})));
    $('jewelMetalLabel').textContent = colours ? 'Colour' : 'Metal';
    if(colours){
      $('jewelIntro').textContent = isShoes()
        ? "We compare pairs with the one you bought, using the store's own heel heights and colours. Nothing is read from your feet or body."
        : "We compare bags with the one you bought, using the store's own widths and colours. Nothing is read from you.";
      $('jewelNote').placeholder = isShoes() ? 'The heels are too high and the red is too loud.' : 'Too big and too bright. Nothing extra.';
      $('jewelSizeLabel').textContent = isShoes() ? 'Lower or higher heel' : 'Smaller or larger';
      $('jewelSize').options[1].textContent = isShoes() ? 'Lower heel' : 'Smaller';
      $('jewelSize').options[2].textContent = isShoes() ? 'Higher heel' : 'Larger';
      $('jewelConfirmBtn').textContent = isShoes() ? 'Find shoes' : 'Find bags';
      return;
    }
    $('jewelSize').options[1].textContent = 'Smaller or shorter';
    $('jewelSize').options[2].textContent = 'Larger or longer';
    $('jewelPrefs').querySelector('h2').textContent = necklace ? 'Say what should change.' : 'Say what should change.';
    $('jewelIntro').textContent = necklace
      ? "We compare necklaces with the one you bought, using the store's own lengths and metals. Nothing is read from your neck or face."
      : "We compare pairs with the one you bought, using the store's own lengths and metals. Nothing is read from your face.";
    $('jewelNote').placeholder = necklace ? 'Too long, and I wanted gold. Nothing extra.' : 'Too long, and I wanted silver. Nothing extra.';
    $('jewelSizeLabel').textContent = necklace ? 'Shorter or longer' : 'Smaller or larger';
    $('jewelConfirmBtn').textContent = necklace ? 'Find necklaces' : 'Find earrings';
  }
  const CONTROLS={
    lightness:v=>{$('lightness').value=v;return $('lightness');},
    tone:v=>{$('tone').value=v;return $('tone');},
    intensity:v=>{$('intensity').value=v;return $('intensity');},
    quieter_print:v=>{$('quieter').checked=v;return $('quieter');},
    sleeve_required:v=>{$('sleeve').value=v;return $('sleeve');},
    color_preferences:v=>{$('color').value=v.join(', ');return $('color');},
    neckline_preference:v=>{$('neckline').value=v;return $('neckline');},
    size:v=>{$('jewelSize').value=v;return $('jewelSize');},
    metal:v=>{$('jewelMetal').value=v;return $('jewelMetal');},
    max_top_up_minor:v=>{const box=isBeauty()?$('shadeBudget'):isJewellery()?$('jewelBudget'):$('budget');
      box.value=(v/100).toFixed(2);return box;},
  };
  function fill(field,value){
    const control=CONTROLS[field](value);control.classList.add('filled');
    setTimeout(()=>control.classList.remove('filled'),2500);
  }
  $('interpretBtn').onclick=()=>interpretWords($('note'),$('understood'));
  $('copilotBtn').onclick=()=>runCopilot();
  $('copilotBack').onclick=()=>show('prefs');
  const choicesGuide=document.createElement('button');choicesGuide.type='button';choicesGuide.className='button secondary';
  choicesGuide.id='choicesCopilotBtn';choicesGuide.textContent='Plan my next step';choicesGuide.onclick=()=>runCopilot();
  $('compareBtn').after(choicesGuide);
  async function runCopilot(revisit=null){
    if(confirmingTopPreferences) return;
    $('copilotBtn').disabled=true;choicesGuide.disabled=true;
    const box=$('copilotResult');box.replaceChildren();
    const waiting=document.createElement('p');waiting.setAttribute('role','status');waiting.textContent='Checking your next step…';box.append(waiting);show('copilot');
    try{
      const data=await api('/api/sessions/'+sessionId+'/copilot','POST',{intent:$('note').value,revisit});
      const source=document.createElement('p');source.className='small';source.textContent=data.reader==='claude'
        ? 'AI tool plan. The explanation below is built from checked tool facts.'
        : data.fallback ? 'Rules guide after the AI plan was unavailable. No change was confirmed.' : 'Rules guide. No model call was made.';
      const summary=document.createElement('p');summary.className='question';summary.setAttribute('role','status');summary.textContent=data.summary;
      box.replaceChildren(source,summary);
      if(data.trace?.length){const details=document.createElement('details');const title=document.createElement('summary');title.textContent='Checks performed';
        const list=document.createElement('ol');for(const step of data.trace){const li=document.createElement('li');li.textContent=step.label;list.append(li);}details.append(title,list);box.append(details);}
      if(data.proposal&&Object.keys(data.proposal).length){const review=document.createElement('button');review.type='button';review.className='button secondary';review.textContent='Review proposed controls';
        review.onclick=()=>{for(const [field,value] of Object.entries(data.proposal)){if(CONTROLS[field])fill(field,value);}show('prefs');};box.append(review);}
      for(const question of data.questions||[]){const ask=document.createElement('p');ask.textContent=question.question;box.append(ask);
        const row=document.createElement('div');row.className='actions';
        for(const option of question.options||[]){const answer=document.createElement('button');answer.type='button';answer.className='button secondary';answer.textContent=option.label;
          answer.onclick=()=>{if(question.field==='revisit')runCopilot(option.value);else if(CONTROLS[question.field]){fill(question.field,option.value);ask.textContent=question.question+' Answer: '+option.label+'. Review the controls before confirming.';row.remove();}};row.append(answer);}box.append(row);}
      if(data.status==='REVIEW_TRADEOFF'){const action=document.createElement('button');action.type='button';action.className='button secondary';action.textContent='Show this trade-off';
        action.onclick=async()=>{show('prefs');try{await renderCandidates($('noMatch'));const select=$('tradeoffConstraint');if(select){select.value=data.selected_limit;select.form.requestSubmit();}}catch(error){notice(error.message);}};box.append(action);}
      if(['COMPARE_OPTIONS','REVIEW_PREVIEW'].includes(data.status)){const action=document.createElement('button');action.type='button';action.className='button secondary';action.textContent='Inspect current choices';
        action.onclick=async()=>{try{if(JSON.stringify(choices.map(x=>x.sku))!==JSON.stringify(data.options.map(x=>x.sku)))await renderCandidates($('noMatch'));else show('choices');}catch(error){notice(error.message);}};box.append(action);
        if(data.image_review_pending){const note=document.createElement('p');note.className='small';note.textContent='Requested-change image assessments are still unassessed for some choices.';box.append(note);}
        for(const review of data.reviews||[]){if(review.status!=='ASSESSED')continue;const item=document.createElement('p');item.textContent=review.sku+': '+review.label+' — '+review.assessment;box.append(item);}}
    }catch(error){box.textContent=error.message;}
    finally{$('copilotBtn').disabled=false;choicesGuide.disabled=false;}
  }
  $('shadeInterpretBtn').onclick=()=>interpretWords($('shadeNote'),$('shadeUnderstood'));
  $('jewelInterpretBtn').onclick=()=>interpretWords($('jewelNote'),$('jewelUnderstood'));
  async function interpretWords(input, box){
    const text=input.value.trim();
    if(!text)return notice('Write a few words first, or use the controls below.');
    try{
      const data=await api('/api/sessions/'+sessionId+'/interpret','POST',{text});
      Object.entries(data.proposal).forEach(([field,value])=>fill(field,value));
      const parts=[];
      const lead=document.createElement('p');
      lead.textContent=data.understood.length?'We understood: '+data.understood.join(' · ')+'.':'We could not match your words to the controls. Use the controls below.';
      parts.push(lead);
      for(const q of data.questions){
        const ask=document.createElement('p');ask.className='question';ask.textContent=q.question;parts.push(ask);
        if(q.options.length){
          const row=document.createElement('div');row.className='actions';
          for(const option of q.options){
            const b=document.createElement('button');b.type='button';b.className='button secondary';b.textContent=option.label;
            b.onclick=()=>{fill(q.field,option.value);ask.textContent=q.question+' Answer: '+option.label+'.';row.remove();};
            row.append(b);
          }
          parts.push(row);
        }
      }
      for(const n of data.notes){const p=document.createElement('p');p.className='small';p.textContent=n;parts.push(p);}
      const tail=document.createElement('p');tail.className='small';tail.textContent=data.notice;parts.push(tail);
      box.replaceChildren(...parts);box.hidden=false;notice('');
    }catch(e){notice(e.message);}
  }
  let draftKey=null;
  async function choose(item,card,changed='',accept=false){
    chosen=item;
    try{
      const data=await api('/api/sessions/'+sessionId+'/quotes','POST',{selected_sku:item.sku,accept_preview_concern:!!accept,preview_concern_token:accept||"",preview_job_id:displayedPreviewJobs[item.sku]||null});
      quoteId=data.quote_id;draftKey=crypto.randomUUID();$('draftBtn').disabled=false;
      $('quoteChanged').textContent=changed?changed+' Here is the current quote. Review it and confirm again.':'';
      $('quoteChanged').hidden=!changed;
      $('quote').textContent=data.title+(data.size_label?', size '+data.size_label:'')+'. Eligible credit: '+money(data.credit_minor)+' · Replacement: '+money(data.replacement_minor)+
        ' · Fee: '+money(data.fee_minor)+' · Extra: '+money(data.top_up_minor)+
        '. Quote expires '+new Date(data.expires_at).toLocaleTimeString()+'.';
      show('review');notice('');
    }catch(e){
      $('review').hidden=true;
      if(e.code==='PREVIEW_CONCERN'&&card){
        // The gate found that the preview misses the request; the shopper may still choose, knowingly.
        let box=card.querySelector('.gate-choice');
        if(!box){box=document.createElement('div');box.className='gate-choice';box.setAttribute('role','status');card.append(box);}
        const why=document.createElement('p');why.textContent=e.message+' Look at the preview and the product photo before choosing.';
        const anyway=document.createElement('button');anyway.type='button';anyway.className='button secondary';anyway.textContent='Choose it anyway';
        anyway.setAttribute('aria-label','Choose it anyway: '+item.title);anyway.onclick=()=>{box.remove();
          if(displayedPreviewJobs[item.sku]!==e.previewJobId)return notice('The preview changed. Choose the item again to review its current warning.');
          choose(item,card,changed,e.concernToken);};
        box.replaceChildren(why,anyway);show('choices');anyway.focus();return;
      }
      notice(changed?changed+' It can no longer be quoted: '+e.message+' Choose another option.':e.message);
      if(changed)show($('sizes').hidden?'choices':'sizes');
    }
  }
  const TERMINAL=['succeeded','failed','uncertain'];
  const STATE_WORDS={pending:'Queued',running:'Creating',succeeded:'Ready',failed:'Not available',uncertain:'Unclear'};
  let personLabel='';
  function trustPanel(trust){
    const panel=document.createElement('details');panel.className='preview-trust';
    if(trust.attention){panel.classList.add('attention');panel.open=true;}
    const summary=document.createElement('summary');summary.textContent='Can I trust this preview?';panel.append(summary);
    const lead=document.createElement('p');lead.className='trust-lead';lead.textContent=trust.summary;panel.append(lead);
    const section=(title,lines)=>{const heading=document.createElement('h4');heading.textContent=title;
      const list=document.createElement('ul');for(const text of lines){const li=document.createElement('li');li.textContent=text;list.append(li);}
      panel.append(heading,list);};
    section('What you asked for',trust.requested.length?trust.requested:['No visual-change controls confirmed.']);
    section('From the catalog and exchange rules',trust.catalog_facts);
    section('Checked in this image',trust.checks.map(c=>c.label+': '+c.note+(c.value?' ('+c.value+')':'')));
    section('What remains unverified',trust.limits);
    const source=document.createElement('p');source.className='small';source.textContent=trust.source;
    const link=document.createElement('a');link.href=trust.evaluation_url;link.textContent='How we evaluate these checks';
    panel.append(source,link);return panel;
  }
  function renderJob(job){
    if(displayedPreviewJobs[job.sku]!==job.job_id)return;
    const card=$('choices').querySelector('[data-sku="'+CSS.escape(job.sku)+'"]');
    if(!card)return;
    // Polling returns the same job many times; the status region changes, and is announced, only when it does.
    const key=[job.job_id,job.state,job.image_url,job.message,job.colour_check?.note,job.size_check?.note,job.gate?.note,job.retry_allowed,
      JSON.stringify(job.swatches),job.changes?.url,JSON.stringify(job.trust)].join('|');
    if(card.querySelector('.preview-status')?.dataset.key===key)return;
    card.querySelector('.gate-choice')?.remove();
    previewRevision++;$('advice').hidden=true;
    const frame=card.querySelector('.card-visual');
    let preview=frame.querySelector('.visual-preview');
    const name=card.querySelector('h3').textContent;
    if(job.image_url){
      if(!preview){preview=document.createElement('figure');preview.className='visual-preview';frame.append(preview);}
      const img=document.createElement('img');img.src=job.image_url;
      // Shoes are at the foot of a full-length photo, so their previews are cropped from the bottom.
      if(isShoes())img.classList.add('show-feet');
      img.alt='AI preview of '+name+(personLabel?' on '+personLabel:'');
      // The verdict is drawn on the preview itself, so it reads before the details below. The status line
      // under the card says the same in full, so the label is hidden from screen readers.
      const holder=document.createElement('div');holder.className='preview-frame';holder.append(img);
      const badgeText=job.baseline?'Bought':job.gate?.badge;
      if(badgeText){const badge=document.createElement('span');badge.className='gate-badge';
        badge.dataset.verdict=job.baseline?'reference':job.gate.status;badge.textContent=badgeText;
        badge.setAttribute('aria-hidden','true');holder.append(badge);}
      const caption=document.createElement('figcaption');caption.className='preview-label';caption.textContent=job.label;
      preview.replaceChildren(holder,caption);frame.classList.add('two-up');
    }else if(preview){preview.remove();frame.classList.remove('two-up');}
    let box=card.querySelector('.preview-status');
    // After the name, price and choice, so the decision reads first and the checks follow it.
    if(!box){box=document.createElement('div');box.className='preview-status';box.setAttribute('role','status');card.append(box);}
    box.dataset.state=job.state;box.dataset.key=key;
    const pill=document.createElement('span');pill.className='status-pill';pill.textContent=STATE_WORDS[job.state]||job.state;
    const parts=[pill];
    if(!job.image_url){const text=document.createElement('p');text.textContent=job.message;parts.push(text);}
    if(job.state==='pending'||job.state==='running'){const bar=document.createElement('div');bar.className='progress';parts.push(bar);}
    for(const check of [job.colour_check, job.size_check]){
      if(!check)continue;
      const line=document.createElement('p');line.className='colour-check';
      line.dataset.status=check.status;line.textContent=check.note;parts.push(line);
      // The catalog colour beside the colour measured on this preview, so a colour flag can be seen, not just read.
      if(check===job.colour_check&&job.swatches){const row=document.createElement('p');row.className='swatches';
        for(const [label,hex] of [['Product photo',job.swatches.product],['This preview',job.swatches.preview]]){
          const item=document.createElement('span');item.className='swatch-item';
          const chip=document.createElement('span');chip.className='swatch';chip.style.background=hex;
          chip.setAttribute('aria-hidden','true');item.append(chip,label);row.append(item);}
        parts.push(row);}}
    if(job.gate){const gate=document.createElement('p');gate.className='gate';gate.dataset.status=job.gate.status;
      gate.textContent=job.gate.note;parts.push(gate);
      card.dataset.gate=job.gate.status;}
    if(job.changes&&preview){const img=preview.querySelector('img'),shown=img.alt,toggle=document.createElement('button');
      toggle.type='button';toggle.className='button secondary';toggle.textContent='Show what changed';
      toggle.setAttribute('aria-pressed','false');toggle.setAttribute('aria-label','Show what changed: '+name);
      const legend=document.createElement('p');legend.className='changes-legend';legend.hidden=true;
      const pct=job.changes.outside_change_pct;
      legend.textContent='Blue: what the try-on changed inside the garment area. Orange: changes outside it, where the face, '
        +'hair and edges should stay as photographed'+(pct===null||pct===undefined?'.':': '+pct+'% of that area changed; '
        +job.changes.stable_pct+'% or less counts as stable.');
      toggle.onclick=()=>{const on=toggle.getAttribute('aria-pressed')!=='true';toggle.setAttribute('aria-pressed',String(on));
        img.src=on?job.changes.url:job.image_url;legend.hidden=!on;
        img.alt=on?'What changed between the photo and the preview of '+name+', marked in blue and orange':shown;};
      parts.push(toggle,legend);}
    if(job.rendered_url&&preview){const img=preview.querySelector('img'),drawn=document.createElement('button');
      drawn.type='button';drawn.className='button secondary';drawn.textContent='Show it as YouCam drew it';
      drawn.setAttribute('aria-pressed','false');
      drawn.onclick=()=>{const on=drawn.getAttribute('aria-pressed')!=='true';drawn.setAttribute('aria-pressed',String(on));
        img.src=on?job.rendered_url:job.image_url;};
      parts.push(drawn);}
    if(job.trust)parts.push(trustPanel(job.trust));
    if(job.state==='succeeded'&&!job.baseline&&job.provider==='replay'&&currentLine.category==='tops'){
      const review=document.createElement('button');review.type='button';review.className='button secondary';review.textContent='Review requested changes';
      review.setAttribute('aria-label','Review requested changes: '+card.querySelector('h3').textContent);
      const result=document.createElement('div');result.className='visual-review';result.setAttribute('role','status');
      review.onclick=async()=>{review.disabled=true;result.textContent='Checking requested-change assessment…';
        try{const data=await api('/api/previews/'+job.job_id+'/visual-review','POST',{});renderVisualReview(result,data);}
        catch(error){result.textContent=error.message;}finally{review.disabled=false;}};
      parts.push(review,result);
    }
    if(job.state==='failed'||job.state==='uncertain'){const note=document.createElement('p');note.className='small';
      note.textContent='Your options and quote are unaffected.';parts.push(note);}
    if(job.retry_allowed){const again=document.createElement('button');again.type='button';again.className='button secondary';
      again.textContent='Try this preview again';again.onclick=()=>retry(job);parts.push(again);}
    if(job.state==='succeeded'&&!job.baseline&&lookOffered()){const more=document.createElement('button');more.type='button';
      more.className='button secondary';more.textContent='See it with the rest of my order';
      more.setAttribute('aria-label','See it with the rest of my order: '+card.querySelector('h3').textContent);
      more.onclick=()=>requestLooks(job.sku);parts.push(more);}
    box.replaceChildren(...parts);
  }
  function renderVisualReview(box,data){
    const title=document.createElement('p');title.className='question';title.textContent=data.label;
    const message=document.createElement('p');message.textContent=data.message;box.replaceChildren(title,message);
    if(data.status==='ASSESSED'){
      const source=document.createElement('p');source.className='small';source.textContent=data.model+' · '+data.source;box.append(source);
      const list=document.createElement('ul');for(const check of data.checks){const li=document.createElement('li');li.textContent=check.attribute+' — '+check.finding+': '+check.observation;list.append(li);}box.append(list);
      const evidence=document.createElement('a');evidence.href='/evaluation/agents';evidence.textContent='See the model-review evidence';box.append(evidence);
    }
  }
  async function retry(job){
    try{const fresh=await api('/api/previews/'+job.job_id+'/retry','POST',{});
      if(displayedPreviewJobs[job.sku]!==job.job_id)return;
      displayedPreviewJobs[job.sku]=fresh.job_id;previewRevision++;renderJob(fresh);pollJobs([fresh]);notice('');}
    catch(e){notice(e.message);}
  }
  async function pollJobs(jobs,started=Date.now(),all=jobs){
    const open=jobs.filter(j=>!TERMINAL.includes(j.state));
    const elapsed=Date.now()-started;
    const fetchAll=list=>Promise.all(list.map(j=>api('/api/previews/'+j.job_id).catch(()=>j)));
    // A jewellery size check is measured against the pair bought, so it can change after its own job ends:
    // once every job is done, each is fetched once more.
    if(!open.length){
      const complete=jobs===all?jobs:await fetchAll(all);
      if(jobs!==all)complete.forEach(renderJob);
      // A finished top's gate verdict is written a moment after the job ends, so ask again until it is there.
      if(complete.some(j=>j.gate_pending)&&elapsed<150000){
        await new Promise(r=>setTimeout(r,1000));
        const again=await fetchAll(complete.filter(j=>j.gate_pending));
        again.forEach(renderJob);
        const merged=complete.map(j=>again.find(a=>a.job_id===j.job_id)||j);
        return pollJobs(merged,started,merged);
      }
      if($('notice').textContent==='Previews are being prepared.'){
        notice(!complete.every(j=>j.state==='succeeded')?'Some previews could not complete. Your options and quote are unaffected.'
          :complete.every(j=>j.provider==='replay')?'Recorded results shown. No new AI run and no units used.'
          :complete.every(j=>j.provider==='mock')?'Test previews complete. No image was generated or sent to YouCam.':'Previews complete.',false);
      }
      return;
    }
    if(elapsed>150000)return;
    await new Promise(r=>setTimeout(r,elapsed<30000?2000:4000));
    const fresh=await fetchAll(open);
    fresh.forEach(renderJob);
    pollJobs(fresh,started,all);
  }
  // A look: the choice with the rest of the order on the same photo, each item added in turn. Offered when
  // returning a top, since a look needs a photo of the upper body, and in recorded mode only once looks are recorded.
  const LOOK_PARTS = ['lip','necklace'];
  function lookOffered(){
    if(!currentLine || currentLine.category !== 'tops' || !personAssetId) return false;
    if(previewMode === 'recorded' && $('look').dataset.recorded !== 'yes') return false;
    return orderItems.some(item => LOOK_PARTS.includes(item.category));
  }
  async function requestLooks(sku){
    const item = choices.find(c => c.sku === sku);
    // The same comparison as the previews: what the shopper has now, made the same way, beside the choice.
    const skus = $('baselineCheck').checked ? [currentLine.sku, sku] : [sku];
    try{
      const looks = [];
      for(const s of skus) looks.push(await api('/api/sessions/'+sessionId+'/looks','POST',
        {sku:s, consent:true, asset_id:personAssetId, mode:previewMode}));
      $('lookIntro').textContent = (item ? item.title : 'Your choice') + ' with the rest of your order, added one item at a '
        + 'time to the same photo of ' + personLabel + '. A preview is approximate.';
      $('lookCards').replaceChildren();
      looks.forEach(renderLook);
      const leftOut = looks[0].left_out;
      $('lookLeftOut').textContent = leftOut.length ? 'Left out: ' + leftOut.join(' ') : '';
      $('lookLeftOut').hidden = !leftOut.length;
      show('look'); notice('');
      pollLooks(looks);
    }catch(e){ notice(e.message); }
  }
  function renderLook(look){
    let card = $('lookCards').querySelector('[data-look="'+look.look_id+'"]');
    const key = [look.state, look.image_url, look.message, JSON.stringify(look.trust), ...look.items.map(i => i.state)].join('|');
    if(card && card.dataset.key === key) return;
    if(!card){ card = document.createElement('article'); card.className = 'card look-card'; card.dataset.look = look.look_id;
      $('lookCards').append(card); }
    card.dataset.key = key;
    const names = look.items.map(i => i.title);
    const title = document.createElement('h3');
    title.textContent = look.baseline ? 'What you have now' : 'With ' + names[0];
    const parts = [title];
    if(look.image_url){
      const img = document.createElement('img'); img.src = look.image_url;
      img.alt = 'AI preview of ' + names.join(', ') + (personLabel ? ' on ' + personLabel : '');
      parts.push(img);
    }
    const status = document.createElement('div'); status.className = 'preview-status'; status.dataset.state = look.state;
    status.setAttribute('role', 'status');
    const pill = document.createElement('span'); pill.className = 'status-pill';
    pill.textContent = STATE_WORDS[look.state] || look.state;
    const message = document.createElement('p'); message.className = look.state === 'succeeded' ? 'preview-label' : '';
    message.textContent = look.message;
    status.append(pill, message);
    if(look.state === 'pending' || look.state === 'running'){ const bar = document.createElement('div'); bar.className = 'progress';
      status.append(bar); }
    const steps = document.createElement('ol'); steps.className = 'look-steps';
    for(const item of look.items){
      const li = document.createElement('li');
      li.textContent = item.feature + ': ' + item.title + ' · ' + (STATE_WORDS[item.state] || 'Waiting')
        + (item.colour_check ? ' · ' + item.colour_check.note : '');
      steps.append(li);
    }
    card.replaceChildren(...parts, status, steps, ...(look.trust?[trustPanel(look.trust)]:[]));
  }
  async function pollLooks(looks, started=Date.now()){
    const open = looks.filter(l => !TERMINAL.includes(l.state));
    const elapsed = Date.now() - started;
    if(!open.length || elapsed > 240000) return;
    await new Promise(r => setTimeout(r, elapsed < 30000 ? 2000 : 4000));
    const fresh = await Promise.all(open.map(l => api('/api/looks/'+l.look_id).catch(() => l)));
    fresh.forEach(renderLook);
    pollLooks(fresh, started);
  }
  $('lookBack').onclick = () => { $('look').hidden = true; show('choices'); };
  let personAssetId=null, personSource='', pendingFile=null;
  const selectedPerson=()=>(document.querySelector('input[name="person"]:checked')||{}).value||'';
  // Recorded results exist only for the demo people, so a shopper's own photo always uses a live preview.
  function setPreviewModes(){
    const recordedHere = currentLine && recordedCategories().includes(currentLine.category) && selectedPerson() !== 'upload';
    $('recordedOption').hidden = !recordedHere;
    const live = !$('liveOption').hidden;
    const mode = document.querySelector('input[name="previewMode"][value="' + (recordedHere || !live ? 'recorded' : 'live') + '"]');
    if(mode) mode.checked = true;
  }
  // Both entry points grant this browser access in place. Unlocking never selects a live preview or requests one.
  const judgeDialog=$('judgeAccessDialog');
  let judgeUnlockBusy=false;
  function applyJudgeAccess(data){
    if(data.allow_upload){$('uploadOption').dataset.allowed='1';$('uploadOption').hidden=isBag();}
    if(data.live_label){$('liveText').textContent=data.live_label;$('liveOption').hidden=false;}
    ['judgeCode','judgeAccessCode'].forEach(id=>{if($(id))$(id).value='';});
    ['judgeUnlock','judgeAccessForm'].forEach(id=>{if($(id))$(id).hidden=true;});
    $('judgeAccessBtn').textContent='Judge access active';
    $('judgeAccessBtn').dataset.granted='1';
    $('judgeAccessIntro').hidden=true;
    judgeDialog.setAttribute('aria-describedby','judgeAccessSuccessTitle');
    $('judgeAccessSuccess').hidden=false;
  }
  async function unlockJudgeAccess(fromDialog){
    if(judgeUnlockBusy)return;
    const input=$(fromDialog?'judgeAccessCode':'judgeCode');
    const error=$('judgeAccessError');
    const showError=message=>{
      if(fromDialog){error.textContent=message;error.hidden=false;}
      else notice(message);
      if(!fromDialog||judgeDialog.open)input.focus();
    };
    const code=input.value.trim();
    if(!code){showError('Enter the judge access code first.');return;}
    if(error){error.textContent='';error.hidden=true;}
    judgeUnlockBusy=true;
    const buttons=[$('judgeAccessSubmit'),$('judgeUnlockBtn')].filter(Boolean);
    const labels=buttons.map(button=>button.textContent);
    buttons.forEach(button=>{button.disabled=true;button.textContent='Unlocking…';});
    if($('judgeAccessForm'))$('judgeAccessForm').setAttribute('aria-busy','true');
    try{
      const data=await api('/api/judge-access','POST',{code});
      applyJudgeAccess(data);
      if(judgeDialog.open)$('judgeAccessSuccessTitle').focus();
      else if(!fromDialog){
        notice('Judge access is on. Your order stays open. Choose a live preview when you want a new run.',false);
        ($('uploadOption').hidden?$('liveOption'):$('uploadOption')).querySelector('input').focus();
      }
    }catch(e){showError(e.message);}
    finally{
      judgeUnlockBusy=false;
      buttons.forEach((button,index)=>{button.disabled=false;button.textContent=labels[index];});
      if($('judgeAccessForm'))$('judgeAccessForm').removeAttribute('aria-busy');
    }
  }
  $('judgeAccessBtn').onclick=()=>{
    judgeDialog.showModal();
    ($('judgeAccessBtn').dataset.granted==='1'?$('judgeAccessSuccessTitle'):$('judgeAccessTitle')).focus();
  };
  $('judgeAccessClose').onclick=() => judgeDialog.close();
  $('judgeAccessContinue').onclick=() => judgeDialog.close();
  judgeDialog.addEventListener('close',()=>{
    if($('judgeAccessCode'))$('judgeAccessCode').value='';
    if($('judgeAccessError')){$('judgeAccessError').textContent='';$('judgeAccessError').hidden=true;}
    $('judgeAccessBtn').focus();
  });
  if($('judgeAccessForm'))$('judgeAccessForm').onsubmit=e=>{e.preventDefault();unlockJudgeAccess(true);};
  if($('judgeUnlockBtn')){
    $('judgeUnlockBtn').onclick=()=>unlockJudgeAccess(false);
    $('judgeCode').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();unlockJudgeAccess(false);}});
  }
  document.querySelectorAll('input[name="person"]').forEach(radio=>radio.addEventListener('change',()=>{
    document.querySelectorAll('input[name="person"]').forEach(r=>delete r.dataset.auto);
    if($('uploadArea'))$('uploadArea').hidden=selectedPerson()!=='upload';
    setPreviewModes();
  }));
  // What a usable photo looks like for each kind of preview.
  const UPLOAD_GUIDANCE = {
    person: ['One adult, standing and facing the camera.', 'Chest up, with both shoulders in the photo.',
      'The whole face visible, with no hand, hair or sunglasses covering it.',
      'The person fills most of the frame, ideally against a plain background.'],
    face: ['One adult, facing the camera, with the face filling much of the frame.',
      'The whole face visible, with no hand, hair or glasses covering it.', 'Even light and no beauty filter.'],
    earrings: ['One adult, facing the camera or turned slightly, with an ear showing.',
      'Hair tucked behind the ear and no earrings that hide the earlobe.', 'Even light and no beauty filter.'],
    hand: ['One hand, with the back of the hand towards the camera.', 'All five nails showing, ideally bare.',
      'A plain background and even light.'],
    full: ['One adult, standing and facing the camera, head to toe.', 'Both feet fully visible, with nothing in front of them.',
      'A plain background and even light.'],
  };
  function setUploadGuidance(kind){
    if(!$('uploadGuidance'))return;
    const items=[...UPLOAD_GUIDANCE[kind], 'A JPEG or PNG at least 512 by 384 pixels and at most 9.5 MB.'];
    $('uploadGuidance').replaceChildren(...items.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
  }
  async function checkPhoto(file){
    if(!['image/jpeg','image/png'].includes(file.type))return 'Use a JPEG or PNG photo.';
    if(file.size>9500000)return 'This photo is larger than 9.5 MB.';
    let bitmap=null;
    try{bitmap=await createImageBitmap(file);}catch(e){return 'This file is not a usable photo.';}
    const long=Math.max(bitmap.width,bitmap.height),short=Math.min(bitmap.width,bitmap.height);
    if(long<512||short<384)return 'This photo is too small. Use one at least 512 by 384 pixels.';
    if(long>4096)return 'This photo is too large. Use one at most 4096 pixels on its longest side.';
    if(long/short>2.5)return 'This photo is too narrow or too wide. Use an ordinary portrait or landscape photo.';
    return '';
  }
  // A photo comes from a file or, on a phone, straight from the camera; the newest choice wins.
  const photoInputs=['photoFile','photoCamera'].filter(id=>$(id));
  photoInputs.forEach(id=>$(id).onchange=async()=>{
    const file=$(id).files[0];
    photoInputs.filter(other=>other!==id).forEach(other=>$(other).value='');
    pendingFile=null;
    if(personSource==='upload')personAssetId=null;
    if(!file){$('photoPreview').hidden=true;return;}
    const problem=await checkPhoto(file);
    if(problem){$(id).value='';$('photoPreview').hidden=true;return notice(problem);}
    pendingFile=file;$('photoThumb').src=URL.createObjectURL(file);$('photoPreview').hidden=false;notice('');
  });
  if($('photoDelete'))$('photoDelete').onclick=async()=>{
    const sent=personSource==='upload'&&personAssetId;
    if(sent){try{await api('/api/assets/'+personAssetId,'DELETE');}catch(e){}}
    if(personSource==='upload')personAssetId=null;
    pendingFile=null;photoInputs.forEach(id=>$(id).value='');$('photoPreview').hidden=true;
    notice(sent?'Your photo was deleted from Rechoice. If a preview already used it, YouCam keeps its copy for up to 30 days.':'');
  };
  async function uploadPhoto(file){
    const form=new FormData();form.append('image',file);form.append('adult_permission','true');
    const res=await fetch('/api/sessions/'+sessionId+'/assets',{method:'POST',credentials:'same-origin',
      headers:{'X-CSRFToken':window.CSRF_TOKEN},body:form});
    const data=await res.json();
    if(!res.ok)throw new Error(data.error?.message||'Upload failed.');
    return data;
  }
  async function personForPreview(choice){
    if(personAssetId&&personSource===choice)return personAssetId;
    if(choice==='upload'){
      if(!pendingFile)throw new Error('Choose a photo to upload.');
      if(!$('adultCheck').checked)throw new Error('Confirm the adult permission statement.');
      const data=await uploadPhoto(pendingFile);
      $('photoThumb').src=data.image_url;
      personAssetId=data.asset_id;personLabel='your photo';
    }else{
      const data=await api('/api/sessions/'+sessionId+'/demo-person','POST',{model:choice.slice(5)});
      personAssetId=data.asset_id;personLabel=data.label;
    }
    personSource=choice;
    return personAssetId;
  }
  $('previewBtn').onclick=()=>{
    const nails = currentLine && currentLine.category === 'nail';
    const hair = currentLine && currentLine.category === 'hair';
    const earrings = isJewellery() && !isNecklace() && !isAccessory();
    const necklace = isNecklace();
    const shoes = isShoes(), bag = isBag();
    $('photoPurpose').textContent = isBeauty()
      ? 'To show the shades on a person, Rechoice sends that person\'s photo and the shade colours to YouCam, an AI try-on service.'
      : earrings ? 'To show the earrings on a person, Rechoice sends that person\'s photo and the product photos to YouCam, an AI try-on service.'
      : necklace ? 'To show the necklaces on a person, Rechoice sends that person\'s photo and the product photos to YouCam, an AI try-on service.'
      : shoes ? 'To show the shoes on a person, Rechoice sends that person\'s photo and the product photos to YouCam, an AI try-on service.'
      : bag ? 'To show the bags on a person, Rechoice sends that person\'s photo and the product photos to YouCam, an AI try-on service. YouCam also needs to be told whether the person is a woman or a man, so bags use the demo people, whose details are recorded.'
      : 'To draw the two tops on a person, Rechoice sends that person\'s photo and the product photos to YouCam, an AI try-on service.';
    $('photoGuidance').textContent = nails ? 'Nail polish needs a hand: one hand, back of the hand towards the camera, bare nails.'
      : hair ? 'Hair colour needs your hair in the photo: head and shoulders, hair down and in even light.'
      : isBeauty() ? 'Makeup needs a face: front-facing, lips and cheeks visible, in even light.'
      : earrings ? 'Earrings need a face with an ear showing: front-facing or turned slightly, hair behind the ear.'
      : shoes ? 'Shoes need the whole person, head to toe, with both feet fully visible.' : '';
    $('photoGuidance').hidden = !(isBeauty() || earrings || shoes);
    // A bag needs the person's gender, which Rechoice never guesses from a photo, so only the demo people offer it.
    // Personal photos are offered only where uploads are allowed: to judges, or when the server opens them.
    $('uploadOption').hidden = bag || !$('uploadOption').dataset.allowed;
    if(bag && selectedPerson()==='upload') $('uploadOption').querySelector('input').checked=false;
    // Offer only the demo people this item can be previewed on: a hand for nails, a face for makeup or earrings.
    const kind = nails ? 'hand' : shoes ? 'full' : hair ? 'person' : (isBeauty() || earrings) ? 'face' : 'person';
    let offered = 0;
    document.querySelectorAll('#demoGrid .demo-option').forEach(option => {
      const fits = option.dataset.kind === kind;
      option.hidden = !fits;
      if(fits) offered++; else option.querySelector('input').checked = false;
    });
    $('demoGrid').hidden = !offered;
    // Start on the demo person whose recorded results cover this item: Model B for tops, where the gate
    // flags one of the recorded renders, otherwise the first of the right kind. A person the shopper picked stays.
    const picked = document.querySelector('input[name="person"]:checked');
    if(!picked || picked.dataset.auto){
      const visible = [...document.querySelectorAll('#demoGrid .demo-option')].filter(o=>!o.hidden);
      const tops = kind === 'person' && !hair && !necklace && !bag;
      const start = (tops && visible.find(o=>o.querySelector('input').value === 'demo:model-b')) || visible[0];
      if(picked) delete picked.dataset.auto;
      if(start){const input = start.querySelector('input'); input.checked = true; input.dataset.auto = '1';}}
    $('demoUnavailable').textContent = 'There is no demo ' + (nails ? 'hand' : kind === 'face' ? 'face' : 'model') + ' here, so use a photo of your own.';
    $('demoUnavailable').hidden = offered > 0;
    // Recorded results cover the suggested choices on one demo person for each kind of item that has them.
    setPreviewModes();
    setUploadGuidance(earrings ? 'earrings' : kind);
    $('recordedText').textContent = 'Recorded results: real YouCam results from our pilots, shown at once with no new AI run ('
      + (nails ? 'Hand A covers the suggested shades' : hair ? 'Model A covers the suggested shades' : isBeauty() ? 'Face A covers the suggested shades'
      : earrings ? 'Face A covers the suggested earrings' : necklace ? 'Model A covers the suggested necklaces'
      : shoes ? 'Model F covers the suggested shoes' : bag ? 'Model A covers the suggested bags'
      : 'Models A, B and C cover the suggested tops; on Model B the gate flags one of them') + ')';
    $('baselineCheck').parentElement.lastChild.textContent = isBeauty()
      ? ' Also show the shade I bought on the same person, so the comparison is fair (3 previews instead of 2)'
      : earrings ? ' Also show the pair I bought on the same person, so the comparison is fair (3 previews instead of 2)'
      : necklace ? ' Also show the necklace I bought on the same person, so the comparison is fair (3 previews instead of 2)'
      : shoes ? ' Also show the pair I bought on the same person, so the comparison is fair (3 previews instead of 2)'
      : bag ? ' Also show the bag I bought on the same person, so the comparison is fair (3 previews instead of 2)'
      : ' Also show my current top on the same person, so the comparison is fair (3 previews instead of 2)';
    show('photo');
  };
  $('photoBack').onclick=()=>{$('photo').hidden=true;show('choices');};
  $('compareBtn').onclick=async()=>{
    const box=$('advice');box.hidden=false;box.replaceChildren(Object.assign(document.createElement('p'),{textContent:'Comparing your options…'}));
    $('compareBtn').disabled=true;
    const revision=previewRevision;
    try{
      const data=await api('/api/sessions/'+sessionId+'/compare','POST',{preview_job_ids:Object.values(displayedPreviewJobs)});
      if(revision!==previewRevision)return;
      const summary=document.createElement('p');summary.className='advice-summary';summary.textContent=data.summary;
      const list=document.createElement('ul');
      for(const choice of choices){if(!data.notes[choice.sku])continue;
        const li=document.createElement('li');const name=document.createElement('b');name.textContent=choice.title+': ';
        li.append(name,data.notes[choice.sku].replace(choice.title+': ',''));list.append(li);}
      const label=document.createElement('p');label.className='small';label.textContent=data.label;
      box.replaceChildren(summary,list,label);
    }catch(e){box.replaceChildren(Object.assign(document.createElement('p'),{textContent:e.message}));}
    finally{$('compareBtn').disabled=false;}
  };
  $('consentBtn').onclick=async()=>{
    const choice=selectedPerson();
    if(!choice)return notice('Choose a demo model or your own photo first.');
    if(!$('consentCheck').checked)return notice('Confirm that you understand how the photo is used.');
    try{
      const assetId=await personForPreview(choice);
      const withOriginal=$('baselineCheck').checked;
      const mode=(document.querySelector('input[name="previewMode"]:checked')||{}).value||'recorded';
      previewMode=mode;$('look').hidden=true;
      const data=await api('/api/sessions/'+sessionId+'/previews','POST',
        {candidate_skus:choices.map(c=>c.sku),consent:true,asset_id:assetId,include_original:withOriginal,mode});
      displayedPreviewJobs=Object.fromEntries(data.jobs.map(j=>[j.sku,j.job_id]));
      previewRevision++;$('advice').hidden=true;
      const baseline=data.jobs.find(j=>j.baseline);
      if(baseline){
        $('baselineCard').dataset.sku=baseline.sku;
        const figure=$('baselineCard').querySelector('.visual-source');
        figure.replaceChildren(visualFor(currentLine));
        if(currentLine.image_label){const caption=document.createElement('figcaption');caption.className='source-label';
          caption.textContent=currentLine.image_label;figure.append(caption);}
        $('baselineCard').querySelector('.eyebrow').textContent=isBeauty()?'The shade you bought, for comparison'
          :isNecklace()?'The necklace you bought, for comparison':isBag()?'The bag you bought, for comparison'
          :isJewellery()?'The pair you bought, for comparison':'Your current top, for comparison';
        $('baselineTitle').textContent=currentLine.title;
      }
      $('baselineWrap').hidden=!baseline;
      data.jobs.forEach(renderJob);
      $('previewSubject').textContent='Previews show '+personLabel+'. Each sits beside the catalog photo and is approximate.';
      $('previewSubject').hidden=false;
      $('photo').hidden=true;show('choices');
      const done=data.jobs.every(j=>TERMINAL.includes(j.state));
      notice(!done?'Previews are being prepared.':data.provider==='mock'
        ?'Test previews complete. No image was generated or sent to YouCam.'
        :data.provider==='replay'?'Recorded results shown. No new AI run and no units used.':'',false);
      pollJobs(data.jobs);
    }catch(e){notice(e.message);}
  };
  let requestId=null, refreshTimer=null;
  const remember=(key,value)=>{try{value===null?localStorage.removeItem(key):localStorage.setItem(key,value);}catch(e){}};
  const recall=key=>{try{return localStorage.getItem(key);}catch(e){return null;}};
  function renderRequest(data){
    $('requestState').textContent=data.state_text;
    const asked=data.message&&data.state==='NEEDS_CLARIFICATION';
    $('staffQuestion').hidden=!data.message;
    $('staffMessage').textContent=data.message+(data.reply?' Your answer: '+data.reply:'');
    $('staffQuestion').querySelector('.question').textContent=asked?'Staff asked:':'Message from staff:';
    $('replyArea').hidden=!data.can_reply;
    if(['REVIEWED','DECLINED'].includes(data.state)&&refreshTimer){clearInterval(refreshTimer);refreshTimer=null;}
  }
  async function refreshRequest(){
    if(!requestId)return;
    try{renderRequest(await api('/api/requests/'+requestId));}
    catch(e){remember('secondlook:request',null);}
  }
  function watchRequest(){
    if(refreshTimer)clearInterval(refreshTimer);
    const until=Date.now()+600000;
    refreshTimer=setInterval(()=>{if(Date.now()>until){clearInterval(refreshTimer);refreshTimer=null;}else refreshRequest();},20000);
  }
  $('refreshBtn').onclick=()=>refreshRequest();
  $('replyBtn').onclick=async()=>{
    const reply=$('replyText').value.trim();
    if(!reply)return notice('Write your answer first.');
    try{renderRequest(await api('/api/requests/'+requestId+'/reply','POST',{reply}));$('replyText').value='';notice('');}
    catch(e){notice(e.message);}
  };
  if(recall('secondlook:request')){
    requestId=recall('secondlook:request');
    api('/api/requests/'+requestId).then(data=>{
      $('doneText').textContent='Your earlier request is saved.';renderRequest(data);show('done',false);watchRequest();
    }).catch(()=>{remember('secondlook:request',null);requestId=null;});
  }
  $('draftBtn').onclick=async()=>{
    // One key per quote and no second click while sending: a double click returns the same draft.
    if($('draftBtn').disabled)return;
    $('draftBtn').disabled=true;
    try{
      const data=await api('/api/requests','POST',{quote_id:quoteId,quote_version:1,confirmed:true},
        {'Idempotency-Key':draftKey});
      $('doneText').textContent='Request '+data.request_id+' is saved for staff review.';
      requestId=data.request_id;remember('secondlook:request',requestId);
      $('review').hidden=true;show('done');notice('');
      await refreshRequest();watchRequest();
    }catch(e){
      if(e.status===409&&!['SESSION_CLOSED','PREVIEW_PENDING'].includes(e.code)&&chosen){await choose(chosen,$('choices').querySelector('[data-sku="'+CSS.escape(chosen.sku)+'"]'),'The quote changed: '+e.message);return;}
      $('draftBtn').disabled=false;notice(e.message);
    }
  };
})();
