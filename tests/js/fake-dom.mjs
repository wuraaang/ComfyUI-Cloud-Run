class FakeEvent {
  constructor(type, target) {
    this.type = type;
    this.target = target;
    this.defaultPrevented = false;
  }

  preventDefault() {
    this.defaultPrevented = true;
  }
}

export class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.children = [];
    this.style = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.id = "";
    this.className = "";
    this.type = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.open = false;
    this._textContent = "";
  }

  set textContent(value) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this._textContent = String(value ?? "");
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  get isConnected() {
    let current = this;
    while (current) {
      if (current === this.ownerDocument.body || current === this.ownerDocument.head) {
        return true;
      }
      current = current.parentNode;
    }
    return false;
  }

  setAttribute(name, value) {
    const stringValue = String(value);
    this.attributes.set(name, stringValue);
    if (name === "id") this.id = stringValue;
    if (name === "class") this.className = stringValue;
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  append(...children) {
    for (const child of children) {
      if (typeof child === "string") {
        const text = new FakeElement("#text", this.ownerDocument);
        text.textContent = child;
        this.appendChild(text);
      } else {
        this.appendChild(child);
      }
    }
  }

  replaceChildren(...children) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this._textContent = "";
    this.append(...children);
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  async dispatchEvent(event) {
    for (const listener of this.listeners.get(event.type) ?? []) {
      await listener(event);
    }
    return !event.defaultPrevented;
  }

  async click() {
    if (this.disabled) return;
    if (this.tagName === "INPUT" && this.type === "radio") this.checked = true;
    await this.dispatchEvent(new FakeEvent("click", this));
  }

  showModal() {
    this.open = true;
  }

  async close() {
    this.open = false;
    await this.dispatchEvent(new FakeEvent("close", this));
  }

  focus() {}

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter(
      (child) => child !== this,
    );
    this.parentNode = null;
  }
}

function findById(root, id) {
  if (root.id === id) return root;
  for (const child of root.children) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

export class FakeDocument {
  constructor() {
    this.readyState = "complete";
    this.head = new FakeElement("head", this);
    this.body = new FakeElement("body", this);
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }

  getElementById(id) {
    return findById(this.head, id) ?? findById(this.body, id);
  }
}
