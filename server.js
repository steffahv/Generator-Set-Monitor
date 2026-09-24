const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

app.get('/images/yofc_logo.png', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'images', 'yofc_logo.png'));
});

app.use(express.static(path.join(__dirname, 'public')));

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    app: 'myapp-ge',
    timestamp: new Date().toISOString(),
  });
});

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
  console.log(`myapp-ge running at http://localhost:${PORT}`);
});
