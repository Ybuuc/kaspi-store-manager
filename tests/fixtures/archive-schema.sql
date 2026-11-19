-- Schema only; no shop/customer records. From user-provided kaspibot.zip.
CREATE TABLE import_batches (
	id INTEGER NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	file_hash VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (file_hash)
);
CREATE TABLE incoming_items (
	id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	qty INTEGER NOT NULL, 
	purchase_price INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);
CREATE TABLE kaspi_order_sync (
	id INTEGER NOT NULL, 
	external_key VARCHAR(120) NOT NULL, 
	order_code VARCHAR(64) NOT NULL, 
	entry_id VARCHAR(64), 
	product_id INTEGER, 
	bucket_name VARCHAR(40), 
	cancelled_at DATETIME, 
	delivered_at DATETIME, 
	archived_at DATETIME, 
	title VARCHAR(255), 
	qty INTEGER NOT NULL, 
	sale_sum INTEGER NOT NULL, 
	customer_name VARCHAR(255), 
	customer_phone VARCHAR(50), 
	state VARCHAR(50), 
	status_text VARCHAR(100), 
	detailed_state VARCHAR(120), 
	detailed_status VARCHAR(120), 
	is_onway_created BOOLEAN NOT NULL, 
	is_sale_created BOOLEAN NOT NULL, 
	is_cancelled BOOLEAN NOT NULL, 
	raw_json TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);
CREATE TABLE onway_items (
	id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	sale_price INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	kaspi_order_key VARCHAR(120), 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);
CREATE TABLE parser_jobs (
	id INTEGER NOT NULL, 
	active_slot VARCHAR(20), 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	config_json TEXT NOT NULL, 
	message TEXT, 
	processed INTEGER NOT NULL, 
	errors INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (active_slot)
);
CREATE TABLE parser_observations (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	product_id INTEGER, 
	kaspi_id VARCHAR(120) NOT NULL, 
	created_at DATETIME NOT NULL, 
	data_json TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES parser_jobs (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);
CREATE TABLE product_aliases (
	id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	alias VARCHAR(255) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	UNIQUE (alias)
);
CREATE TABLE products (
	id INTEGER NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	purchase_price INTEGER NOT NULL, 
	stock_qty INTEGER NOT NULL, 
	reserved_qty INTEGER NOT NULL, 
	onway_qty INTEGER NOT NULL, 
	kaspi_product_id VARCHAR(120), 
	sku VARCHAR(120), 
	category_name VARCHAR(255), 
	image_url TEXT, 
	current_price INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	last_synced_at DATETIME, 
	kaspi_raw_json TEXT, 
	kaspi_url TEXT, 
	netcost INTEGER NOT NULL, 
	is_top_speed BOOLEAN NOT NULL, 
	city_name VARCHAR(120), 
	city_price INTEGER NOT NULL, 
	min_price INTEGER NOT NULL, 
	max_price INTEGER NOT NULL, 
	step_price INTEGER NOT NULL, 
	position INTEGER NOT NULL, 
	first_position_cost INTEGER NOT NULL, 
	preorder INTEGER NOT NULL, 
	remainder INTEGER NOT NULL, 
	pickup_point_name VARCHAR(120), 
	is_demping BOOLEAN NOT NULL, 
	is_increase BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE stock_movements (
	id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	qty_change INTEGER NOT NULL, 
	note VARCHAR(255), 
	sale_sum INTEGER NOT NULL, 
	delivery_cost INTEGER NOT NULL, 
	commission_pct INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	external_code VARCHAR(120), 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);