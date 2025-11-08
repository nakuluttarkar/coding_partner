# SimpleWebCalculator

## Description
SimpleWebCalculator is a lightweight, web‑based calculator built with plain HTML, CSS, and JavaScript. It offers a clean, responsive interface that works on any modern browser and device. The calculator supports basic arithmetic operations, a clear button, backspace, and keyboard shortcuts for quick input.

## Demo
Open the project in your browser by navigating to the project folder and double‑clicking `index.html` (or right‑click → Open with → Your browser). No server or build step is required.

## Features
1. **Basic arithmetic** – addition, subtraction, multiplication, and division.
2. **Clear** – reset the current input.
3. **Backspace** – delete the last character.
4. **Keyboard support** – numbers, operators, and the Enter key for equals.
5. **Responsive design** – adapts to mobile, tablet, and desktop screens.
6. **Accessibility** – ARIA labels, focus styles, and semantic markup.

## Technologies
- **HTML5** – Semantic structure and form‑like input.
- **CSS3** – Flexbox layout, responsive breakpoints, and custom properties.
- **JavaScript (ES6)** – Event handling, expression evaluation, and UI updates.

## Installation / Running
1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/simple-web-calculator.git
   cd simple-web-calculator
   ```
2. No build or dependency installation is required.
3. Open `index.html` in any modern web browser.

## Usage
- **On‑screen buttons** – Click the numbers and operators to build an expression.
- **Keyboard shortcuts** – Type numbers and operators directly; press **Enter** to evaluate, **Backspace** to delete, and **Esc** to clear.
- **Result** – The calculated result appears in the read‑only display at the top.

## File Structure
```
/simple-web-calculator
├── index.html          # Main HTML markup
├── style.css           # Stylesheet with responsive layout
├── script.js           # JavaScript for calculator logic
└── assets/             # Optional assets (currently unused)
```
- **index.html** – Contains the calculator layout and ARIA attributes.
- **style.css** – Defines the visual design, Flexbox grid, and media queries.
- **script.js** – Implements button click handling, expression evaluation, and keyboard support.
- **assets/** – Reserved for images or icons if needed in future.

## Contributing
Feel free to fork the repository, create a feature branch, and submit a pull request. Please keep the code consistent with the existing style and add tests if you introduce new functionality.

## License
This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.
