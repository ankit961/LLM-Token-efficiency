import { render } from "react";

export class Widget extends Component {
  draw() {
    return render(this.state);
  }
}

export function mount(el) {
  return new Widget(el);
}
