-- ============================================================
-- V001 — Baseline test fixture data
-- ============================================================
-- MySQL Docker entrypoint runs *.sql in alpha order on first
-- volume init. Full reset:  docker compose down -v && docker compose up
-- ============================================================

USE deepferry;

-- ── Schema version tracking ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema_version (
    version     VARCHAR(20)  PRIMARY KEY,
    description VARCHAR(200) NOT NULL,
    applied_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO schema_version (version, description)
VALUES ('V001', 'Baseline test fixture: customers, products, orders, order_items, product_reviews');

-- ── 1. customers (7 rows) ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS customers (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    name         VARCHAR(100)  NOT NULL,
    status       VARCHAR(20)   NOT NULL DEFAULT 'normal',
    credit_limit DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    email        VARCHAR(200)  DEFAULT NULL,
    metadata     JSON          DEFAULT NULL,
    created_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO customers (id, name, status, credit_limit, email, metadata) VALUES
(1, 'Alice Johnson', 'vip',    50000.00, 'alice@example.com',   '{"tags": ["enterprise", "priority"]}'),
(2, 'Bob Smith',     'vip',    75000.00, 'bob@example.com',     '{"tags": ["enterprise"]}'),
(3, 'Charlie Brown', 'normal', 10000.00, 'charlie@example.com', '{"tags": ["individual"]}'),
(4, 'Diana Prince',  'normal', 15000.00, 'diana@example.com',   NULL),
(5, 'Eve Brown',     'normal', 20000.00, 'eve@example.com',     '{"tags": ["startup"]}'),
(6, 'Frank White',   'normal', 30000.00, 'frank@example.com',   NULL),
(7, 'Grace Lee',     'normal', 25000.00, 'grace@example.com',   NULL)
ON DUPLICATE KEY UPDATE name = VALUES(name);

-- ── 2. products (10 rows) ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS products (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(100)  NOT NULL,
    price      DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO products (id, name, price) VALUES
(1,  'Laptop',      8999.00),
(2,  'Mouse',         149.00),
(3,  'Keyboard',      349.00),
(4,  'Monitor',      2499.00),
(5,  'Headphones',    599.00),
(6,  'Webcam',        399.00),
(7,  'USB Hub',       129.00),
(8,  'Desk Lamp',     199.00),
(9,  'Printer',      1599.00),
(10, 'Scanner',      1299.00)
ON DUPLICATE KEY UPDATE name = VALUES(name);

-- ── 3. orders (11 rows — 9 in June 2026) ──────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    customer_id  INT            NOT NULL,
    status       VARCHAR(20)    NOT NULL DEFAULT 'pending',
    total_amount DECIMAL(12,2)  NOT NULL DEFAULT 0.00,
    order_date   DATE           NOT NULL,
    created_at   TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO orders (id, customer_id, status, total_amount, order_date) VALUES
(1,  1, 'completed',  1500.00, '2026-06-01'),
(2,  2, 'completed',  5000.00, '2026-06-02'),
(3,  2, 'completed',  4200.00, '2026-06-15'),
(4,  3, 'completed',   800.00, '2026-06-03'),
(5,  3, 'cancelled',   900.00, '2026-06-12'),
(6,  4, 'completed',  1200.00, '2026-06-04'),
(7,  5, 'completed', 10000.00, '2026-06-05'),
(8,  5, 'completed', 12000.00, '2026-06-20'),
(9,  6, 'completed',  3500.00, '2026-06-06'),
(10, 1, 'completed',  2300.00, '2026-07-10'),
(11, 6, 'completed',  1800.00, '2026-07-05')
ON DUPLICATE KEY UPDATE status = VALUES(status);

-- ── 4. order_items (11 rows) ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS order_items (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    order_id   INT NOT NULL,
    product_id INT NOT NULL,
    quantity   INT NOT NULL DEFAULT 1,
    FOREIGN KEY (order_id)   REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO order_items (id, order_id, product_id, quantity) VALUES
(1,  1,  1, 1),
(2,  1,  2, 2),
(3,  2,  1, 2),
(4,  2,  4, 1),
(5,  3,  5, 3),
(6,  4,  6, 1),
(7,  7,  1, 1),
(8,  8,  2, 5),
(9,  8,  4, 2),
(10, 9,  7, 4),
(11, 10, 3, 1)
ON DUPLICATE KEY UPDATE quantity = VALUES(quantity);

-- ── 5. product_reviews (8 rows — products 9,10 intentionally left) ────

CREATE TABLE IF NOT EXISTS product_reviews (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    product_id INT          NOT NULL,
    rating     TINYINT      NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review     TEXT         DEFAULT NULL,
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO product_reviews (id, product_id, rating, review) VALUES
(1, 1, 5, 'Excellent build quality and battery life'),
(2, 2, 4, 'Comfortable and precise'),
(3, 3, 3, 'Decent but a bit loud'),
(4, 4, 5, 'Crystal clear 4K, great for coding'),
(5, 5, 4, 'Good noise cancellation'),
(6, 6, 3, 'Works fine, mediocre autofocus'),
(7, 7, 4, 'Handy with the extra ports'),
(8, 8, 5, 'Bright and adjustable, saves desk space')
ON DUPLICATE KEY UPDATE rating = VALUES(rating);
