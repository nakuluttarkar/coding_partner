// script.js
// Simple calculator functionality
// Functions: add, subtract, multiply, divide, clear
// Each function reads values from inputs #num1 and #num2,
// performs the operation, and writes the result into the div #result.

/**
 * Retrieve numeric values from the two input fields.
 * Returns an object {a, b} where a and b are numbers (or NaN if parsing fails).
 */
function getInputs() {
    const a = parseFloat(document.getElementById('num1').value);
    const b = parseFloat(document.getElementById('num2').value);
    return { a, b };
}

/**
 * Display a value (or message) inside the result div.
 * @param {string|number} msg - The text to display.
 */
function showResult(msg) {
    const resultDiv = document.getElementById('result');
    resultDiv.textContent = msg;
}

/**
 * Perform addition and display the result.
 */
function add() {
    const { a, b } = getInputs();
    if (isNaN(a) || isNaN(b)) {
        showResult('Please enter valid numbers');
        return;
    }
    showResult(a + b);
}

/**
 * Perform subtraction and display the result.
 */
function subtract() {
    const { a, b } = getInputs();
    if (isNaN(a) || isNaN(b)) {
        showResult('Please enter valid numbers');
        return;
    }
    showResult(a - b);
}

/**
 * Perform multiplication and display the result.
 */
function multiply() {
    const { a, b } = getInputs();
    if (isNaN(a) || isNaN(b)) {
        showResult('Please enter valid numbers');
        return;
    }
    showResult(a * b);
}

/**
 * Perform division and display the result.
 * Handles division by zero gracefully.
 */
function divide() {
    const { a, b } = getInputs();
    if (isNaN(a) || isNaN(b)) {
        showResult('Please enter valid numbers');
        return;
    }
    if (b === 0) {
        showResult('Cannot divide by zero');
        return;
    }
    showResult(a / b);
}

/**
 * Clear the result display and the input fields.
 */
function clear() {
    document.getElementById('result').textContent = '';
    document.getElementById('num1').value = '';
    document.getElementById('num2').value = '';
}

// Attach event listeners once the DOM is fully loaded.
window.addEventListener('DOMContentLoaded', () => {
    document.getElementById('add').addEventListener('click', add);
    document.getElementById('subtract').addEventListener('click', subtract);
    document.getElementById('multiply').addEventListener('click', multiply);
    document.getElementById('divide').addEventListener('click', divide);
    document.getElementById('clear').addEventListener('click', clear);
});
