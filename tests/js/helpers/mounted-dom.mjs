// A minimal DOM for mounting the workspace viewer and inspector in Node: elements with
// children, attributes, listeners (fire() dispatches), dialogs and media methods.
export function mountedDom() {
    class Node {
        constructor(tag) {this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.listeners=new Map();this._text='';this.classList={add(){},remove(){}};}
        get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
        set textContent(value){this._text=String(value);this.children=[];}
        get childNodes(){return this.children;}
        get isConnected(){return this.root || !!this.parent?.isConnected;}
        // As in the DOM, a string argument becomes a text node.
        static wrap(node){if(typeof node!=='string')return node;const text=new Node('#text');text.textContent=node;return text;}
        append(...nodes){for(const node of nodes.map(Node.wrap)){node.parent=this;this.children.push(node);}}
        insertBefore(node,before){node.parent=this;const index=this.children.indexOf(before);this.children.splice(index<0?this.children.length:index,0,node);return node;}
        prepend(...nodes){nodes=nodes.map(Node.wrap);for(const node of nodes)node.parent=this;this.children.unshift(...nodes);}
        replaceChildren(...nodes){for(const child of this.children)child.parent=null;this.children=[];this._text='';this.append(...nodes);}
        remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null;}
        setAttribute(key,value){this.attributes[key]=String(value);}
        getAttribute(key){return this.attributes[key]??null;}
        removeAttribute(key){delete this.attributes[key];}
        addEventListener(type,callback,options={}){const list=this.listeners.get(type)||[];list.push({callback,signal:options.signal});this.listeners.set(type,list);}
        removeEventListener(type,callback){this.listeners.set(type,(this.listeners.get(type)||[]).filter(e=>e.callback!==callback));}
        fire(type,event={}){for(const e of [...(this.listeners.get(type)||[])])if(!e.signal?.aborted)e.callback({target:this,preventDefault(){},...event});}
        focus(){this.focused=true;}
        showModal(){this.open=true;}
        close(){this.open=false;this.fire('close');}
        contains(node){return node===this||this.children.some(c=>c.contains(node));}
        closest(selector){if(selector==='section'&&this.tagName==='section')return this;if(selector==='[data-send-role]'&&this.dataset.sendRole)return this;if(selector==='[data-copy-role]'&&this.dataset.copyRole)return this;return this.parent?.closest(selector)||null;}
        querySelector(selector){return walk(this,n=>n.tagName===selector)[0]||null;}
        pause(){this.paused=true;}
        load(){this.loaded=true;}
        play(){this.played=true;return Promise.resolve();}
    }
    const walk=(node,predicate)=>[...(predicate(node)?[node]:[]),...node.children.flatMap(child=>walk(child,predicate))];
    const oldDocument=globalThis.document,oldWindow=globalThis.window;
    const container=new Node('main');container.root=true;
    const doc=new Node('document');doc.createElement=tag=>new Node(tag);doc.createElementNS=(_,tag)=>new Node(tag);
    doc.querySelectorAll=()=>walk(container,n=>n.tagName==='dialog'&&n.open);doc.activeElement=new Node('button');doc.activeElement.root=true;
    const win=new Node('window');win.location={href:'http://localhost/prompt_manager/workspace'};win.getSelection=()=>({isCollapsed:true});
    globalThis.document=doc;globalThis.window=win;
    return {container,doc,win,walk,Node,restore(){globalThis.document=oldDocument;globalThis.window=oldWindow;}};
}
